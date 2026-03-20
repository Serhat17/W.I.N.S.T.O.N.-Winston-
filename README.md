<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.0-blue" alt="Version">
  <img src="https://img.shields.io/badge/python-3.9%2B-green" alt="Python">
  <img src="https://img.shields.io/badge/tests-408%20passed-brightgreen" alt="Tests">
  <img src="https://img.shields.io/badge/license-AGPL--3.0-orange" alt="License">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey" alt="Platform">
</p>

# W.I.N.S.T.O.N.

**Wildly Intelligent Network System for Task Operations and Navigation**

Your own AI assistant — like Jarvis, but open-source. Runs locally on your Mac or Linux machine with full privacy, or connects to 14+ cloud LLM providers. Talk to it via CLI, Web UI, Telegram, Discord, or WhatsApp.

```
You: Fass mir meine Mails zusammen und trag den Termin morgen um 10 in meinen Kalender ein
Winston: Done — 3 neue Mails zusammengefasst, Termin "Team Sync" für morgen 10:00 erstellt.
```

---

## Why Winston?

Most AI assistants are either cloud-only SaaS products or barebones chatbot wrappers. Winston is neither.

- **Actually useful** — 22 skills that do real things: send emails, manage your calendar, monitor prices, browse the web, run code, generate images
- **Private by default** — Runs with local LLMs via Ollama. No data leaves your machine unless you choose a cloud provider
- **Multi-channel** — One bot that works across Telegram, Discord, WhatsApp, Web UI, and CLI simultaneously
- **Bilingual** — Built for German and English from day one
- **Extensible** — Add a new skill in ~30 lines of Python. Plug in any LLM provider

---

## Features at a Glance

| Category | What it does |
|----------|-------------|
| **LLM Brain** | Ollama (local) + OpenAI, Anthropic, Gemini, DeepSeek, Mistral, xAI, OpenRouter, Perplexity, HuggingFace, and more. Automatic fallback between providers |
| **Voice** | Whisper STT + Piper/ElevenLabs/macOS TTS + wake word detection |
| **Channels** | Telegram, Discord, WhatsApp, Web UI (WebSocket), CLI |
| **Email** | Read, search, compose, send via IMAP/SMTP |
| **Calendar** | Apple Calendar + Google Calendar integration |
| **Web** | DuckDuckGo search, web page fetching, Playwright browser automation (beta) |
| **Notes** | Create, search, manage persistent notes |
| **Files** | Search, read, create files on your machine |
| **Code** | Sandboxed Python execution with safe module whitelist |
| **Images** | Generate images via DALL-E 3, Stability AI, or Pollinations (free) |
| **Price Monitor** | Watch products/flights/hotels — get alerts when prices drop |
| **Shopping** | Order history, shopping lists, browser-based store navigation |
| **Scheduling** | Cron jobs, morning briefings, evening summaries, recurring tasks |
| **Routines** | Multi-step automations: "Good morning" → weather + calendar + news |
| **Memory** | ChromaDB vector search + personality persistence + daily journals |
| **Security** | SSRF guard, rate limiter, prompt injection defense, risk classification, PIN auth |
| **Multi-Agent** | Home / Work / Coding / Shopping personas with separate skill sets and memory |

---

## Quick Start

### Option A: Homebrew (macOS)

```bash
brew install serhatbilge/tap/winston
```

Then install a local AI model and launch:

```bash
brew install ollama && ollama serve &
ollama pull qwen2.5:7b
winston --setup
```

### Option B: One-Line Install

```bash
curl -fsSL https://raw.githubusercontent.com/serhatbilge/W.I.N.S.T.O.N..S/main/install.sh | bash
```

This installs everything automatically — Python, Ollama, a default AI model, and Winston itself. An interactive setup wizard will guide you through connecting Telegram, adding API keys, etc.

After installation, just type:
```bash
winston
```

### Option C: Manual Install

<details>
<summary>Click to expand manual setup steps</summary>

#### 1. Prerequisites

- **Python 3.9+**
- **Ollama** (for local LLMs) — [ollama.ai](https://ollama.ai)

```bash
# macOS
brew install ollama portaudio
ollama serve &
ollama pull qwen2.5:7b
```

#### 2. Install

```bash
git clone https://github.com/serhatbilge/W.I.N.S.T.O.N..S.git
cd W.I.N.S.T.O.N..S

# Automated setup (macOS) — installs everything including BlackHole for Observer Mode
chmod +x setup_mac.sh && ./setup_mac.sh

# Or manual setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

#### 3. Configure

```bash
# Run the interactive setup wizard
python -m winston.main --setup

# Or manually
cp .env.example .env
# Add your API keys (all optional — Ollama works out of the box)
```

</details>

<details>
<summary>Supported API keys in <code>.env</code></summary>

```env
# LLM Providers (all optional — local Ollama works without any keys)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...
DEEPSEEK_API_KEY=...
MISTRAL_API_KEY=...
XAI_API_KEY=...
OPENROUTER_API_KEY=...
PERPLEXITY_API_KEY=...

# Channels
TELEGRAM_BOT_TOKEN=...
DISCORD_BOT_TOKEN=...

# Optional services
ELEVENLABS_API_KEY=...          # Premium TTS
STABILITY_API_KEY=...           # Image generation
AMADEUS_API_KEY=...             # Flight/hotel search
GOOGLE_CALENDAR_CREDENTIALS=... # Google Calendar
```
</details>

### 4. Run

```bash
# CLI mode — just start chatting
winston

# Or explicitly
python -m winston.main --mode text

# Voice mode (microphone + speaker)
python -m winston.main --mode voice

# Server mode (Web UI + Telegram + Discord + WhatsApp)
python -m winston.main --mode server

# Use a specific model
python -m winston.main --model llama3.1:8b
```

---

## Architecture

```
                    ┌─────────────────────────────────────────────────┐
                    │               W.I.N.S.T.O.N. v1.0              │
                    ├────────────────────┬────────────────────────────┤
   Inputs           │     Core Engine    │        Outputs             │
  ─────────         │    ────────────    │       ────────             │
  CLI               │  Brain (LLM)      │  CLI / Terminal            │
  Telegram    ───▶  │  Agent Router     │  Telegram         ───▶     │
  Discord           │  Safety Guard     │  Discord                   │
  WhatsApp          │  Memory           │  WhatsApp                  │
  Web UI            │  Scheduler        │  Web UI                    │
  Voice / Mic       │  Identity         │  Voice / TTS               │
                    ├────────────────────┴────────────────────────────┤
                    │                  22 Skills                      │
                    │                                                 │
                    │  email · web_search · web_fetch · browser       │
                    │  notes · calendar · google_calendar · system    │
                    │  file_manager · clipboard · code_runner         │
                    │  youtube · screenshot · image_gen · travel      │
                    │  price_monitor · shopping · knowledge_base      │
                    │  audio_analysis · scheduler · smart_home        │
                    │  observer                                       │
                    ├─────────────────────────────────────────────────┤
                    │  Security: SSRF · Rate Limit · Injection Guard  │
                    │  Infra: Model Fallback · Channel Health · Retry │
                    └─────────────────────────────────────────────────┘
```

---

## Usage Examples

### Natural Language (CLI or any channel)

```
You: Schick eine Mail an max@example.com — Betreff: Projekt Update
Winston: ✓ Email gesendet an max@example.com

You: What's on my calendar tomorrow?
Winston: Tomorrow you have: 10:00 Team Standup, 14:30 Dentist appointment

You: Watch the price of the Sony WH-1000XM5 on Amazon — alert me under 250€
Winston: ✓ Now monitoring Sony WH-1000XM5. I'll alert you when the price drops below €250.

You: Run this Python code: print(sum(range(1, 101)))
Winston: Output: 5050

You: Generate an image of a futuristic Tokyo at sunset
Winston: ✓ Image generated and saved to ~/.winston/generated_images/...
```

### Telegram Commands

| Command | Description |
|---------|-------------|
| `/help` | All commands |
| `/daily on` | Enable morning briefing |
| `/daily time 07:30` | Set briefing time |
| `/daily city Berlin` | Set weather location |
| `/voice on/off` | Toggle voice replies |
| `/image <prompt>` | Generate an image |
| `/observer on` | Start meeting observer |

### CLI Commands

| Command | Description |
|---------|-------------|
| `/models` | List available LLM models |
| `/model gpt-4o` | Switch model |
| `/skills` | List all 22 skills |
| `/memory` | Memory status |
| `/export` | Export conversation |
| `mach` | Autonomous mode — skip confirmations |
| `mach vorsichtig` | Careful mode — confirm every action |

---

## Adding a Custom Skill

```python
# winston/skills/my_skill.py
from winston.skills.base import BaseSkill, SkillResult

class MySkill(BaseSkill):
    name = "my_skill"
    description = "Describe what it does — the LLM reads this to decide when to use it"
    parameters = {
        "query": {"type": "string", "description": "The user's input"},
    }

    def execute(self, query: str = "", **kwargs) -> SkillResult:
        result = f"You said: {query}"
        return SkillResult(success=True, message=result)
```

Register in `winston/main.py`:
```python
from winston.skills.my_skill import MySkill
# In _register_skills():
skills.append(MySkill())
```

Done. The LLM will automatically route matching requests to your skill.

---

## Project Structure

```
W.I.N.S.T.O.N./
├── winston/
│   ├── main.py                 # CLI orchestrator & entry point
│   ├── server.py               # FastAPI server (Web UI + API + WebSocket)
│   ├── config.py               # Typed configuration (14+ providers)
│   ├── core/
│   │   ├── brain.py            # Multi-provider LLM engine
│   │   ├── providers.py        # Ollama, OpenAI, Anthropic, Gemini providers
│   │   ├── agent_router.py     # Multi-agent personas (Home/Work/Coding/Shopping)
│   │   ├── browser_agent.py    # Multi-step browser automation (beta)
│   │   ├── memory.py           # ChromaDB vector memory
│   │   ├── identity.py         # Personality, user profile, daily journals
│   │   ├── safety.py           # Risk classification & action confirmation
│   │   ├── pipeline.py         # Shared input processing pipeline
│   │   ├── scheduler.py        # APScheduler cron/interval jobs
│   │   ├── routines.py         # Multi-step workflow automation
│   │   ├── monitor_engine.py   # Background price monitoring
│   │   ├── model_fallback.py   # Provider failover with cooldowns
│   │   ├── channel_health.py   # Auto-restart unhealthy channels
│   │   ├── conversations.py    # Conversation persistence
│   │   ├── listener.py         # Whisper STT + wake word
│   │   ├── speaker.py          # ElevenLabs / Piper / macOS TTS
│   │   ├── observer.py         # Background audio observer
│   │   └── usage_tracker.py    # Token usage tracking
│   ├── skills/                 # 22 modular skills (see above)
│   ├── channels/
│   │   ├── telegram_channel.py # Full Telegram bot (voice, images, inline KB)
│   │   ├── discord_channel.py  # Discord bot (DMs + guilds)
│   │   └── whatsapp_channel.py # WhatsApp via WAHA Docker
│   ├── security/
│   │   ├── ssrf_guard.py       # SSRF protection (private IP blocking)
│   │   ├── rate_limiter.py     # Per-client rate limiting
│   │   └── content_wrapper.py  # Prompt injection defense
│   ├── utils/                  # Retry, scraping, caching, chunking, Docker
│   └── web/
│       └── index.html          # Single-file responsive chat UI
├── config/
│   ├── settings.yaml           # Main configuration
│   └── agents.yaml             # Agent personas
├── tests/                      # 408 unit tests
├── setup.py                    # pip install -e .
└── requirements.txt
```

---

## LLM Providers

Winston works with **any combination** of these providers. Set one API key or set them all — Winston automatically falls back between providers if one fails.

| Provider | Local? | Key Required | Best For |
|----------|--------|-------------|----------|
| **Ollama** | Yes | No | Privacy, offline use |
| **OpenAI** | No | Yes | GPT-4o, best quality |
| **Anthropic** | No | Yes | Claude, long context |
| **Google Gemini** | No | Yes | Multimodal, fast |
| **DeepSeek** | No | Yes | Coding, cheap |
| **Mistral** | No | Yes | European, fast |
| **xAI (Grok)** | No | Yes | Real-time knowledge |
| **OpenRouter** | No | Yes | Access to 100+ models |
| **Perplexity** | No | Yes | Search-augmented |

### Recommended Ollama Models

| Model | Size | Best For |
|-------|------|----------|
| `qwen2.5:7b` | ~4.7 GB | All-rounder, multilingual |
| `llama3.1:8b` | ~4.7 GB | Reasoning |
| `mistral:7b` | ~4.1 GB | Fast, English |
| `deepseek-coder:6.7b` | ~3.8 GB | Coding |
| `gemma2:9b` | ~5.4 GB | Google's latest |

---

## Troubleshooting

<details>
<summary><b>Cannot connect to Ollama</b></summary>

```bash
ollama serve          # Start the server
curl localhost:11434  # Test the connection
ollama list           # Check installed models
```
</details>

<details>
<summary><b>No module named 'pyaudio'</b></summary>

```bash
# macOS
brew install portaudio && pip install pyaudio
# Linux
sudo apt-get install portaudio19-dev && pip install pyaudio
```
</details>

<details>
<summary><b>Telegram bot not receiving messages</b></summary>

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Set `TELEGRAM_BOT_TOKEN` in `.env`
3. Send any message to the bot — Winston auto-learns your chat ID
4. Daily briefings will start from the next scheduled time
</details>

<details>
<summary><b>Scheduled tasks not firing</b></summary>

Winston catches up on missed cron jobs automatically on startup. Make sure:
1. The task is enabled: `/daily on` via Telegram
2. Winston was started at least once since enabling
3. Check `~/.winston/schedules.json` for `last_run` timestamps
</details>

---

## Contributing

Winston is built to be extended. Here's how you can help:

- **Add a skill** — Pick something from the ideas below, or bring your own
- **Add a channel** — Slack, Matrix, Signal, SMS...
- **Improve browser automation** — Help the browser agent handle complex SPAs
- **Add tests** — Current: 408 passing. More is always better
- **Translations** — Winston speaks German and English, but more languages welcome
- **Documentation** — Tutorials, guides, video walkthroughs

### Skill Ideas

- Spotify / Apple Music control
- Home Assistant deep integration
- Todoist / Notion sync
- GitHub/GitLab integration
- PDF reading & summarization
- Pomodoro timer
- Translation skill (DeepL)
- Stock/crypto portfolio tracker

### Development

```bash
# Clone and set up
git clone https://github.com/serhatbilge/W.I.N.S.T.O.N..S.git
cd W.I.N.S.T.O.N..S
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run tests
python -m pytest tests/ -q

# Run a single test
python -m pytest tests/test_scheduler.py -v

# Start in dev mode
python -m winston.main --mode text --debug
```

---

## License

AGPL-3.0 License — Free to use, modify, and distribute. If you run a modified version as a service, you must share your changes.

---

<p align="center">
  <i>"As always, sir, a great pleasure watching you work."</i><br>
  <b>— W.I.N.S.T.O.N.</b>
</p>
