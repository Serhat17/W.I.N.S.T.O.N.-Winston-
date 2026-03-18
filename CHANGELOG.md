# Changelog

All notable changes to W.I.N.S.T.O.N. will be documented in this file.

## [1.0.0] - 2026-03-18

### Initial Release

**Core**
- Multi-provider LLM engine: Ollama (local) + OpenAI, Anthropic, Gemini, DeepSeek, Mistral, xAI, OpenRouter, Perplexity, HuggingFace
- Automatic model fallback with cooldowns between providers
- Multi-agent personas: Home, Work, Coding, Shopping — each with separate skills and memory
- ChromaDB vector memory with file-based fallback
- Personality persistence, daily journals, memory curation
- APScheduler-based task scheduling with cron + interval jobs
- Missed cron job catch-up on startup
- Multi-step workflow routines (Good Morning, Good Night, System Check)
- Comprehensive safety system: risk classification, action confirmation, blocked commands

**22 Skills**
- email, web_search, web_fetch, browser (beta), notes, calendar, google_calendar
- system_control, file_manager, clipboard, code_runner, youtube, screenshot
- image_gen (DALL-E 3 / Stability AI / Pollinations), travel, price_monitor
- shopping, knowledge_base, audio_analysis, scheduler, smart_home, observer

**Channels**
- Telegram (voice, images, inline keyboards, /daily briefing)
- Discord (DMs + guild support)
- WhatsApp (via WAHA Docker container)
- Web UI (WebSocket chat, dark/light theme, mobile-friendly)
- CLI (text, voice, hybrid modes)

**Security**
- SSRF protection (private IP/hostname blocking)
- Per-client rate limiting
- Prompt injection defense for external content
- PIN authentication for web UI
- Input sanitization and length limits

**Infrastructure**
- Channel health monitoring with auto-restart
- Token usage tracking
- Retry policies with exponential backoff
- Web page caching (15min TTL)
- Markdown-aware message chunking per channel
- Browser profile persistence (cookies/localStorage)
- Price monitoring with background alerts

**Testing**
- 408 unit tests passing
- Coverage across all core modules, skills, and security
