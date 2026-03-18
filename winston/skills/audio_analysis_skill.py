"""
Audio Analysis Skill - Transcribe and analyze long audio files.
Handles podcasts, meetings, lectures, and other long-form audio content.
Supports transcription, summarization, action item extraction, and explanation.
"""

import logging
from typing import Callable, Optional

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.audio_analysis")

CHUNK_SIZE = 3000  # Characters per transcript chunk for summarization


class AudioAnalysisSkill(BaseSkill):
    """Transcribe and analyze long audio files (podcasts, meetings, lectures)."""

    name = "audio_analysis"
    description = (
        "Analyze long audio files such as podcasts, meetings, and lectures. "
        "Can transcribe audio, generate summaries, extract action items and decisions, "
        "or explain technical content in simple terms. "
        "Use this when the user wants to process or understand audio recordings."
    )
    parameters = {
        "action": "Action: 'transcribe', 'summarize', 'extract_actions', 'explain'",
        "audio_path": "Path to the audio file to analyze",
        "transcript": "(optional) Pre-existing transcript text to skip transcription step",
    }

    def __init__(self, config=None, transcribe_fn: Optional[Callable] = None, brain=None):
        super().__init__(config)
        self._transcribe_fn = transcribe_fn
        self._brain = brain

    def execute(self, **kwargs) -> SkillResult:
        """Execute the requested audio analysis action."""
        action = kwargs.get("action", "summarize")
        audio_path = kwargs.get("audio_path", "")
        transcript = kwargs.get("transcript", "")

        actions = {
            "transcribe": lambda: self._transcribe(audio_path),
            "summarize": lambda: self._summarize(transcript, audio_path),
            "extract_actions": lambda: self._extract_actions(transcript, audio_path),
            "explain": lambda: self._explain(transcript, audio_path),
        }

        handler = actions.get(action)
        if handler:
            return handler()
        return SkillResult(
            success=False,
            message=f"Unknown audio analysis action: '{action}'. "
                    f"Available actions: {', '.join(actions.keys())}",
        )

    # ── Helpers ──────────────────────────────────────────────────────────

    def _get_transcript(self, transcript: str, audio_path: str) -> str:
        """Return the transcript if provided, otherwise transcribe the audio file."""
        if transcript and transcript.strip():
            return transcript.strip()

        if not audio_path:
            raise ValueError("No transcript or audio path provided.")

        if not self._transcribe_fn:
            raise RuntimeError(
                "Transcription function not available. "
                "Please provide a transcript directly or configure a transcription backend."
            )

        logger.info("Transcribing audio file: %s", audio_path)
        result = self._transcribe_fn(audio_path)

        if not result or not result.strip():
            raise ValueError(f"Transcription returned empty result for: {audio_path}")

        return result.strip()

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
        """Split text into chunks of approximately chunk_size characters.

        Splits on sentence boundaries when possible to avoid cutting mid-sentence.
        """
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        remaining = text

        while remaining:
            if len(remaining) <= chunk_size:
                chunks.append(remaining)
                break

            # Try to find a sentence boundary near the chunk size
            split_at = chunk_size
            for sep in (". ", ".\n", "! ", "? ", "\n\n", "\n"):
                boundary = remaining.rfind(sep, 0, chunk_size)
                if boundary > chunk_size // 2:
                    split_at = boundary + len(sep)
                    break

            chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()

        return chunks

    # ── Actions ──────────────────────────────────────────────────────────

    def _transcribe(self, audio_path: str) -> SkillResult:
        """Transcribe an audio file and return the full text."""
        if not audio_path:
            return SkillResult(success=False, message="No audio file path provided.")

        if not self._transcribe_fn:
            return SkillResult(
                success=False,
                message="Transcription function not available. "
                        "Please configure a transcription backend.",
            )

        try:
            transcript = self._transcribe_fn(audio_path)
            if not transcript or not transcript.strip():
                return SkillResult(
                    success=False,
                    message=f"Transcription returned empty result for: {audio_path}",
                )
            return SkillResult(
                success=True,
                message=transcript.strip(),
                data={"transcript": transcript.strip(), "audio_path": audio_path},
                speak=False,
            )
        except Exception as e:
            logger.error("Transcription failed for '%s': %s", audio_path, e)
            return SkillResult(success=False, message=f"Transcription failed: {e}")

    def _summarize(self, transcript: str, audio_path: str) -> SkillResult:
        """Summarize a long audio recording via chunked transcript analysis."""
        try:
            text = self._get_transcript(transcript, audio_path)
        except (ValueError, RuntimeError) as e:
            return SkillResult(success=False, message=str(e))

        if not self._brain:
            return SkillResult(
                success=False,
                message="Brain module not available. Cannot generate summary.",
            )

        try:
            chunks = self._chunk_text(text)
            logger.info("Summarizing transcript in %d chunk(s)", len(chunks))

            chunk_summaries = []
            for i, chunk in enumerate(chunks, 1):
                logger.debug("Summarizing chunk %d/%d", i, len(chunks))
                summary = self._brain.think(
                    prompt=chunk,
                    system_override=(
                        "You are a precise audio transcript summarizer. "
                        "Read the following transcript segment and produce a concise summary "
                        "capturing the key points, topics discussed, and any important details. "
                        "Be factual and do not add information not present in the text."
                    ),
                )
                chunk_summaries.append(summary)

            # Combine chunk summaries into one cohesive summary
            combined = "\n\n".join(
                f"[Segment {i}] {s}" for i, s in enumerate(chunk_summaries, 1)
            )

            final_summary = self._brain.think(
                prompt=combined,
                system_override=(
                    "You are a precise summarizer. Below are summaries of consecutive segments "
                    "from a single audio recording. Combine them into one cohesive, well-structured "
                    "summary. Preserve all key points, maintain chronological flow, and eliminate "
                    "redundancy. Use clear headings or bullet points where appropriate."
                ),
            )

            return SkillResult(
                success=True,
                message=final_summary,
                data={
                    "summary": final_summary,
                    "chunk_count": len(chunks),
                    "transcript_length": len(text),
                },
                speak=False,
            )
        except Exception as e:
            logger.error("Summarization failed: %s", e)
            return SkillResult(success=False, message=f"Summarization failed: {e}")

    def _extract_actions(self, transcript: str, audio_path: str) -> SkillResult:
        """Extract action items, decisions, follow-ups, and deadlines from audio."""
        try:
            text = self._get_transcript(transcript, audio_path)
        except (ValueError, RuntimeError) as e:
            return SkillResult(success=False, message=str(e))

        if not self._brain:
            return SkillResult(
                success=False,
                message="Brain module not available. Cannot extract action items.",
            )

        try:
            result = self._brain.think(
                prompt=text,
                system_override=(
                    "You are an expert meeting analyst. Carefully read the following transcript "
                    "and extract all actionable information. Organize your response into these "
                    "sections:\n\n"
                    "## Action Items\n"
                    "List each action item with the responsible person (if mentioned) and any "
                    "deadline.\n\n"
                    "## Decisions Made\n"
                    "List all decisions that were agreed upon.\n\n"
                    "## Follow-ups Required\n"
                    "List items that need follow-up or further discussion.\n\n"
                    "## Deadlines & Dates\n"
                    "List all mentioned deadlines, dates, or time-sensitive items.\n\n"
                    "If a section has no items, write 'None identified.' Be thorough and precise."
                ),
            )

            return SkillResult(
                success=True,
                message=result,
                data={"actions": result, "transcript_length": len(text)},
                speak=False,
            )
        except Exception as e:
            logger.error("Action extraction failed: %s", e)
            return SkillResult(success=False, message=f"Action extraction failed: {e}")

    def _explain(self, transcript: str, audio_path: str) -> SkillResult:
        """Explain the audio content in simple, accessible terms."""
        try:
            text = self._get_transcript(transcript, audio_path)
        except (ValueError, RuntimeError) as e:
            return SkillResult(success=False, message=str(e))

        if not self._brain:
            return SkillResult(
                success=False,
                message="Brain module not available. Cannot generate explanation.",
            )

        try:
            result = self._brain.think(
                prompt=text,
                system_override=(
                    "You are a skilled educator. Read the following transcript from a lecture, "
                    "podcast, or technical recording and explain the content in simple, clear terms "
                    "that anyone can understand. Break down complex concepts, define jargon, and "
                    "use analogies where helpful. Structure your explanation with clear headings "
                    "and keep it accessible to a general audience."
                ),
            )

            return SkillResult(
                success=True,
                message=result,
                data={"explanation": result, "transcript_length": len(text)},
                speak=False,
            )
        except Exception as e:
            logger.error("Explanation generation failed: %s", e)
            return SkillResult(success=False, message=f"Explanation failed: {e}")
