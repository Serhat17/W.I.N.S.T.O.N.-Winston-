"""
YouTube Skill - Search YouTube, get video info, play videos, and get transcripts.
Uses yt-dlp for metadata and subprocess for playback.
"""

import json
import logging
import re
import subprocess
from typing import Optional

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.youtube")


class YouTubeSkill(BaseSkill):
    """Search and control YouTube videos."""

    name = "youtube"
    description = (
        "Search YouTube for videos, play videos, get video information and summaries. "
        "Use this when the user asks to search YouTube, play a video, find a tutorial, "
        "or wants to watch something."
    )
    parameters = {
        "action": "Action: 'search' (find videos), 'play' (open video in browser), 'info' (get video details)",
        "query": "(search) Search query for YouTube",
        "url": "(play/info) YouTube video URL",
        "max_results": "(search) Number of results (default: 5)",
    }

    def execute(self, **kwargs) -> SkillResult:
        action = kwargs.get("action", "search")
        query = kwargs.get("query", "")
        url = kwargs.get("url", "")
        max_results = int(kwargs.get("max_results", 5))

        actions = {
            "search": lambda: self._search(query, max_results),
            "play": lambda: self._play(url or query),
            "info": lambda: self._get_info(url or query),
        }

        handler = actions.get(action)
        if handler:
            return handler()
        return SkillResult(success=False, message=f"Unknown YouTube action: {action}")

    def _search(self, query: str, max_results: int = 5) -> SkillResult:
        """Search YouTube for videos."""
        if not query:
            return SkillResult(success=False, message="No search query provided.")

        try:
            # Use yt-dlp to search YouTube
            result = subprocess.run(
                [
                    "yt-dlp",
                    f"ytsearch{max_results}:{query}",
                    "--dump-json",
                    "--no-download",
                    "--flat-playlist",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                # Fallback: use web search for YouTube
                return self._search_fallback(query, max_results)

            videos = []
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    try:
                        data = json.loads(line)
                        videos.append({
                            "title": data.get("title", "Unknown"),
                            "url": data.get("url", data.get("webpage_url", "")),
                            "channel": data.get("channel", data.get("uploader", "Unknown")),
                            "duration": self._format_duration(data.get("duration")),
                            "views": self._format_views(data.get("view_count")),
                        })
                    except json.JSONDecodeError:
                        continue

            if not videos:
                return self._search_fallback(query, max_results)

            response = f"YouTube results for '{query}':\n\n"
            for i, v in enumerate(videos, 1):
                response += (
                    f"{i}. {v['title']}\n"
                    f"   Channel: {v['channel']} | Duration: {v['duration']} | Views: {v['views']}\n"
                    f"   URL: {v['url']}\n\n"
                )
            response += "Say 'play' followed by the number or title to watch a video."

            return SkillResult(success=True, message=response, data=videos, speak=False)

        except FileNotFoundError:
            return self._search_fallback(query, max_results)
        except Exception as e:
            logger.error(f"YouTube search error: {e}")
            return self._search_fallback(query, max_results)

    def _search_fallback(self, query: str, max_results: int) -> SkillResult:
        """Fallback: search YouTube via DuckDuckGo."""
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                results = list(ddgs.text(
                    f"site:youtube.com {query}",
                    max_results=max_results,
                ))

            if not results:
                return SkillResult(success=True, message=f"No YouTube results for '{query}'.")

            response = f"YouTube results for '{query}':\n\n"
            for i, r in enumerate(results, 1):
                response += (
                    f"{i}. {r.get('title', 'No title')}\n"
                    f"   {r.get('body', '')}\n"
                    f"   URL: {r.get('href', '')}\n\n"
                )

            return SkillResult(success=True, message=response, data=results, speak=False)
        except Exception as e:
            return SkillResult(success=False, message=f"YouTube search failed: {e}")

    def _play(self, url_or_query: str) -> SkillResult:
        """Open a YouTube video in the default browser."""
        if not url_or_query:
            return SkillResult(success=False, message="No video URL or search query provided.")

        # If it's a search query, construct a YouTube search URL
        if not url_or_query.startswith(("http://", "https://")):
            import urllib.parse
            url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(url_or_query)}"
        else:
            url = url_or_query

        try:
            subprocess.Popen(["open", url])
            return SkillResult(success=True, message=f"Opening video in browser.")
        except Exception as e:
            return SkillResult(success=False, message=f"Failed to open video: {e}")

    def _get_info(self, url: str) -> SkillResult:
        """Get detailed info about a YouTube video."""
        if not url:
            return SkillResult(success=False, message="No video URL provided.")

        try:
            result = subprocess.run(
                ["yt-dlp", "--dump-json", "--no-download", url],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                return SkillResult(success=False, message="Could not get video info.")

            data = json.loads(result.stdout)
            info = (
                f"Title: {data.get('title', 'Unknown')}\n"
                f"Channel: {data.get('channel', data.get('uploader', 'Unknown'))}\n"
                f"Duration: {self._format_duration(data.get('duration'))}\n"
                f"Views: {self._format_views(data.get('view_count'))}\n"
                f"Upload Date: {data.get('upload_date', 'Unknown')}\n"
                f"Description: {data.get('description', 'No description')[:300]}...\n"
            )

            return SkillResult(success=True, message=info, data=data, speak=False)
        except FileNotFoundError:
            return SkillResult(success=False, message="yt-dlp not installed. Install with: brew install yt-dlp")
        except Exception as e:
            return SkillResult(success=False, message=f"Failed to get video info: {e}")

    @staticmethod
    def _format_duration(seconds: Optional[int]) -> str:
        if not seconds:
            return "N/A"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @staticmethod
    def _format_views(count: Optional[int]) -> str:
        if not count:
            return "N/A"
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        if count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)
