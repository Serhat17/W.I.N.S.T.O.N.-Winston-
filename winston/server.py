"""
W.I.N.S.T.O.N. Web Server
Exposes WINSTON as a web API + mobile-friendly chat UI.
Access from any device on the same network (phone, tablet, etc.)

Security features:
- PIN-based authentication (displayed on server startup terminal)
- Safety guardrails for all skill executions
- Action confirmation via WebSocket for HIGH-risk actions
- Rate limiting per client
- Input sanitization
"""

import asyncio
import json
import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from winston.config import WinstonConfig, load_config, save_env_value
from winston.core.brain import Brain
from winston.core.listener import Listener
from winston.core.memory import Memory
from winston.core.safety import SafetyGuard, WebAuthenticator, RiskLevel, RiskOverride
from winston.security.rate_limiter import RateLimiter
from winston.core.pipeline import (
    parse_override, detect_fallback_calls, refine_response,
    finalize_response, compact_memory, AUTO_APPROVE_SKILLS,
    is_shopping_intent, SHOP_PROFILES,
)
from winston.skills.base import SkillResult
from winston.skills.email_skill import EmailSkill
from winston.skills.web_search import WebSearchSkill
from winston.skills.system_control import SystemControlSkill
from winston.skills.notes_skill import NotesSkill
from winston.skills.screenshot_skill import ScreenshotSkill
from winston.skills.youtube_skill import YouTubeSkill
from winston.skills.file_manager_skill import FileManagerSkill
from winston.skills.clipboard_skill import ClipboardSkill
from winston.skills.calendar_skill import CalendarSkill
from winston.skills.smart_home_skill import SmartHomeSkill
from winston.skills.code_runner_skill import CodeRunnerSkill
from winston.skills.scheduler_skill import SchedulerSkill
from winston.skills.audio_analysis_skill import AudioAnalysisSkill
from winston.skills.travel_skill import TravelSkill
from winston.skills.google_calendar_skill import GoogleCalendarSkill
from winston.skills.price_monitor_skill import PriceMonitorSkill
from winston.skills.image_gen_skill import ImageGenerationSkill
from winston.skills.web_fetch_skill import WebFetchSkill
from winston.skills.browser_skill import BrowserSkill
from winston.skills.knowledge_base_skill import KnowledgeBaseSkill
from winston.skills.weather_skill import WeatherSkill
from winston.skills.shopping_skill import ShoppingSkill
from winston.core.conversations import ConversationStore
from winston.core.routines import RoutineManager
from winston.core.identity import IdentityManager, MemoryCurator
from winston.core.scheduler import Scheduler
from winston.core.monitor_engine import MonitorEngine
from winston.channels.base import ChannelManager
from winston.core.channel_health import ChannelHealthMonitor
from winston.utils.helpers import setup_logging, get_greeting

logger = logging.getLogger("winston.server")


class WinstonServer:
    """Web server that exposes W.I.N.S.T.O.N. as a REST API + WebSocket chat."""

    def __init__(self, config: WinstonConfig = None):
        self.config = config or load_config()
        setup_logging(self.config.log_file, self.config.debug)

        # Identity system (must be before Brain)
        self.identity = IdentityManager()

        # Initialize core
        self.brain = Brain(self.config, identity=self.identity)
        self.memory = Memory(self.config.memory)

        # Listener for voice/audio transcription (lazy-loaded Whisper)
        try:
            self.listener = Listener(self.config.whisper, self.config.wake_word)
        except Exception as e:
            logger.warning(f"Listener init failed (voice transcription disabled): {e}")
            self.listener = None
        self.curator = MemoryCurator(self.identity, self.brain)
        self.skills = {}
        self._register_skills()

        # Security
        pin = getattr(self.config.server, 'pin', None)
        self.auth = WebAuthenticator(pin=pin)
        self.safety = SafetyGuard(require_confirmation=True)
        self._rate_limiter = RateLimiter(max_requests=30, window_ms=60_000)
        self._ws_rate_limiter = RateLimiter(max_requests=20, window_ms=60_000)
        self._authenticated_ws: dict[WebSocket, str] = {}  # ws -> session token
        self._pending_ws_confirmations: dict[str, asyncio.Future] = {} # action_id -> Future

        # Conversations & Routines
        self.conversations = ConversationStore()
        self.routines = RoutineManager(self.skills)
        self._session_conversations: dict[str, str] = {}  # session -> conversation_id

        # Channels
        self.channel_manager = ChannelManager()
        self._setup_channels()

        # Scheduler
        self.scheduler = Scheduler(
            skills=self.skills,
            routines=self.routines,
            brain=self.brain,
            channel_manager=self.channel_manager,
        )
        # Link scheduler to the scheduler skill
        if "scheduler" in self.skills:
            self.skills["scheduler"].scheduler = self.scheduler

        # Link scheduler + channels to the price monitor skill
        if "price_monitor" in self.skills:
            self.skills["price_monitor"].scheduler = self.scheduler
            self.skills["price_monitor"].channel_manager = self.channel_manager

        # Monitor engine for background price checks
        if "price_monitor" in self.skills:
            self.monitor_engine = MonitorEngine(
                price_monitor_skill=self.skills["price_monitor"],
                channel_manager=self.channel_manager,
                brain=self.brain,
            )
            self.scheduler.monitor_engine = self.monitor_engine
        else:
            self.monitor_engine = None

        # Channel health monitor
        self.health_monitor = ChannelHealthMonitor(self.channel_manager)

        # FastAPI app
        self.app = FastAPI(title="W.I.N.S.T.O.N.", version="0.2.0")
        self._setup_routes()
        self._active_connections: list[WebSocket] = []

    def _register_skills(self):
        """Register all skills."""
        # Audio analysis skill with transcriber + brain injection
        audio_skill = AudioAnalysisSkill(
            transcribe_fn=self.listener.transcribe_file if self.listener else None,
            brain=self.brain,
        )

        # Travel skill with Amadeus config
        travel_skill = TravelSkill(self.config.amadeus)

        # Google Calendar skill (only if configured)
        gcal_skill = GoogleCalendarSkill(self.config.google_calendar)

        # Price monitor skill (scheduler + channels injected after init)
        price_monitor_skill = PriceMonitorSkill()

        for skill in [
            EmailSkill(self.config.email),
            WebSearchSkill(),
            SystemControlSkill(),
            NotesSkill(),
            ScreenshotSkill(),
            YouTubeSkill(),
            FileManagerSkill(),
            ClipboardSkill(),
            CalendarSkill(),
            SmartHomeSkill(),
            CodeRunnerSkill(),
            SchedulerSkill(),
            audio_skill,
            travel_skill,
            gcal_skill,
            price_monitor_skill,
            WebFetchSkill(),
            BrowserSkill(),
            KnowledgeBaseSkill(),
            ImageGenerationSkill(self.config),
            WeatherSkill(),
        ]:
            self.skills[skill.name] = skill

        # Wire ShoppingSkill with reference to BrowserSkill for profile management
        browser_skill = self.skills.get("browser")
        shopping = ShoppingSkill(browser_skill=browser_skill)
        self.skills[shopping.name] = shopping

        self.brain.register_skills(self.skills)

    def _setup_channels(self):
        """Register configured messaging channels."""
        if self.config.channels.telegram.enabled and self.config.channels.telegram.bot_token:
            try:
                from winston.channels.telegram_channel import TelegramChannel
                tg = TelegramChannel(
                    config={
                        "bot_token": self.config.channels.telegram.bot_token,
                        "allowed_users": self.config.channels.telegram.allowed_users,
                        "default_chat_id": self.config.channels.telegram.default_chat_id,
                    },
                    process_fn=self._process_input,
                )
                # Inject ElevenLabs config for Telegram voice replies
                tg._el_api_key = self.config.tts.elevenlabs_api_key
                tg._el_voice_id = self.config.tts.elevenlabs_voice_id
                tg._el_model = self.config.tts.elevenlabs_model
                # Inject transcriber callback for voice message parsing
                if self.listener:
                    tg._transcribe_callback = self.listener.transcribe_file
                self.channel_manager.register(tg)
                logger.info("Telegram channel configured")
            except Exception as e:
                logger.error(f"Failed to configure Telegram: {e}")

        if self.config.channels.discord.enabled and self.config.channels.discord.bot_token:
            try:
                from winston.channels.discord_channel import DiscordChannel
                dc = DiscordChannel(
                    config={
                        "bot_token": self.config.channels.discord.bot_token,
                        "allowed_guilds": self.config.channels.discord.allowed_guilds,
                        "default_channel_id": self.config.channels.discord.default_channel_id,
                    },
                    process_fn=self._process_input,
                )
                # Inject transcriber callback for voice/audio attachments
                if self.listener:
                    dc._transcribe_callback = self.listener.transcribe_file
                self.channel_manager.register(dc)
                logger.info("Discord channel configured")
            except Exception as e:
                logger.error(f"Failed to configure Discord: {e}")

        if self.config.channels.whatsapp.enabled:
            try:
                from winston.channels.whatsapp_channel import WhatsAppChannel
                wa = WhatsAppChannel(
                    config={
                        "waha_url": self.config.channels.whatsapp.waha_url,
                        "waha_api_key": self.config.channels.whatsapp.waha_api_key,
                        "session_name": self.config.channels.whatsapp.session_name,
                        "webhook_port": self.config.channels.whatsapp.webhook_port,
                        "auto_start_docker": self.config.channels.whatsapp.auto_start_docker,
                        "allowed_numbers": self.config.channels.whatsapp.allowed_numbers,
                        "default_chat_id": self.config.channels.whatsapp.default_chat_id,
                    },
                    process_fn=self._process_input,
                )
                if self.listener:
                    wa._transcribe_callback = self.listener.transcribe_file
                self.channel_manager.register(wa)
                logger.info("WhatsApp channel configured")
            except Exception as e:
                logger.error(f"Failed to configure WhatsApp: {e}")

    def _setup_routes(self):
        """Set up all API routes."""
        app = self.app

        # CORS - restricted to localhost and common LAN ranges
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://localhost:*",
                "http://127.0.0.1:*",
                "http://192.168.*:*",
                "http://10.*:*",
            ],
            allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)(:\d+)?$",
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

        # ── Helper: extract & validate auth header ──

        async def require_auth(request: Request) -> str:
            """Dependency that requires a valid session token."""
            token = request.headers.get("Authorization", "").replace("Bearer ", "")
            if not token or not self.auth.validate_session(token):
                raise HTTPException(status_code=401, detail="Unauthorized")
            # Rate limit check
            client_ip = request.client.host if request.client else "unknown"
            rl = self._rate_limiter.consume(client_ip)
            if not rl.allowed:
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded",
                    headers={"Retry-After": str(max(1, rl.retry_after_ms // 1000))},
                )
            return token

        # ── Public routes ──

        @app.get("/", response_class=HTMLResponse)
        async def serve_ui():
            """Serve the chat UI (login page handles auth)."""
            ui_path = Path(__file__).parent / "web" / "index.html"
            # Fallback: if running from installed package, try project root
            if not ui_path.exists():
                project_root = Path(__file__).resolve().parent.parent
                ui_path = project_root / "winston" / "web" / "index.html"
            if not ui_path.exists():
                return HTMLResponse(content="<h1>UI not found</h1><p>index.html missing from web/ directory.</p>", status_code=404)
            return HTMLResponse(content=ui_path.read_text())

        @app.post("/api/auth")
        async def authenticate(request: Request):
            """Authenticate with PIN or token. Returns session token."""
            body = await request.json()
            provided = body.get("pin", "") or body.get("token", "")
            client_ip = request.client.host if request.client else "unknown"

            session = self.auth.authenticate(provided, client_id=client_ip)
            if session:
                return {"status": "ok", "session": session}
            else:
                raise HTTPException(status_code=403, detail="Invalid PIN or token")

        # ── Authenticated routes ──

        @app.get("/api/status")
        async def status(token: str = Depends(require_auth)):
            """Health check and system status."""
            return {
                "status": "online",
                "name": self.config.name,
                "model": self.config.ollama.model,
                "provider": self.brain.get_current_provider(),
                "api_keys": self.brain.get_api_keys(),
                "skills": list(self.skills.keys()),
                "memory": self.memory.get_summary(),
            }

        @app.get("/api/models")
        async def list_models(token: str = Depends(require_auth)):
            """List available models from all providers."""
            models = self.brain.list_models()
            return {
                "models": models,
                "current": {
                    "model": self.config.ollama.model,
                    "provider": self.brain.get_current_provider(),
                }
            }

        @app.post("/api/model/{model_name}")
        async def switch_model(model_name: str, token: str = Depends(require_auth)):
            """Switch the active LLM model."""
            self.brain.change_model(model_name)
            return {"status": "ok", "model": model_name}

        @app.post("/api/chat")
        async def chat(request: Request, token: str = Depends(require_auth)):
            """Send a message and get a response (non-streaming)."""
            body = await request.json()
            user_input = body.get("message", "")
            if not user_input:
                return JSONResponse(
                    status_code=400,
                    content={"error": "No message provided"},
                )

            response = await asyncio.to_thread(self._process_input, user_input)
            return {"response": response, "timestamp": datetime.now().isoformat()}

        @app.websocket("/ws")
        async def websocket_chat(websocket: WebSocket):
            """WebSocket endpoint for real-time streaming chat with auth."""
            await websocket.accept()

            # First message MUST be authentication
            try:
                auth_data = await asyncio.wait_for(websocket.receive_json(), timeout=30)
            except (asyncio.TimeoutError, Exception):
                await websocket.send_json({"type": "auth_required", "message": "Authentication timeout"})
                await websocket.close()
                return

            if auth_data.get("type") != "auth":
                await websocket.send_json({"type": "auth_required", "message": "First message must be auth"})
                await websocket.close()
                return

            client_ip = websocket.client.host if websocket.client else "unknown"
            provided = auth_data.get("pin", "") or auth_data.get("token", "") or auth_data.get("session", "")

            # Check if it's an existing valid session
            if self.auth.validate_session(provided):
                session = provided
            else:
                session = self.auth.authenticate(provided, client_id=client_ip)

            if not session:
                await websocket.send_json({"type": "auth_failed", "message": "Invalid credentials"})
                await websocket.close()
                return

            # Authenticated
            self._authenticated_ws[websocket] = session
            self._active_connections.append(websocket)

            # Resume existing conversation if client provides one, otherwise create new
            resume_conv_id = auth_data.get("conversation_id")
            if resume_conv_id and self.conversations.get_conversation(resume_conv_id):
                self._session_conversations[session] = resume_conv_id
                # Reload memory from the conversation
                conv = self.conversations.get_conversation(resume_conv_id)
                self.memory.clear_session()
                for msg in conv.get("messages", []):
                    self.memory.add_message(msg["role"], msg["content"])
                await websocket.send_json({"type": "auth_ok", "session": session, "conversation_id": resume_conv_id})
                # No greeting on resume — user already knows who we are
            else:
                if session not in self._session_conversations:
                    conv_id = self.conversations.create_conversation()
                    self._session_conversations[session] = conv_id

                active_conv_id = self._session_conversations.get(session)
                await websocket.send_json({"type": "auth_ok", "session": session, "conversation_id": active_conv_id})

                # Send greeting only for new conversations
                greeting = (
                    f"{get_greeting()}, {self.config.user_name}. "
                    f"I am {self.config.name}. How may I assist you?"
                )
                await websocket.send_json({
                    "type": "greeting",
                    "message": greeting,
                    "timestamp": datetime.now().isoformat(),
                })

            try:
                while True:
                    data = await websocket.receive_json()
                    msg_type = data.get("type", "message")

                    # Handle confirmation responses
                    if msg_type == "confirm_action":
                        action_id = data.get("action_id", "")
                        approved = data.get("approved", False)
                        if approved:
                            self.safety.approve_action(action_id)
                        else:
                            self.safety.deny_action(action_id)
                        
                        # Resolve any waiting future in the processing thread
                        if action_id in self._pending_ws_confirmations:
                            self._pending_ws_confirmations[action_id].set_result(approved)
                        continue

                    user_input = data.get("message", "")
                    images = data.get("images", [])
                    if not user_input and not images:
                        continue

                    # WebSocket rate limit check
                    ws_rl = self._ws_rate_limiter.consume(client_ip)
                    if not ws_rl.allowed:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Rate limit exceeded. Please wait a moment.",
                            "retry_after_ms": ws_rl.retry_after_ms,
                        })
                        continue

                    # Save user message to conversation
                    conv_id = self._session_conversations.get(session)
                    if conv_id and (user_input or images):
                        self.conversations.save_message(conv_id, "user", user_input or "", images)

                    # Check for routine triggers
                    routine_id = self.routines.match_routine(user_input) if user_input else None
                    if routine_id:
                        routine = self.routines.get_routine(routine_id)
                        routine_name = routine.get("name", routine_id) if routine else routine_id
                        await websocket.send_json({"type": "thinking"})
                        results = await asyncio.to_thread(
                            self.routines.execute_routine, routine_id
                        )
                        results_text = "\n".join(f"- {r['message']}" for r in results if r)
                        response = self.brain.think(
                            f"I just ran the '{routine_name}' routine. Results:\n{results_text}\n\n"
                            f"Summarize naturally for the user.",
                            conversation_history=self.memory.get_context_messages(),
                        )
                        response = self.brain.strip_skill_blocks(response) if '"skill"' in response else response
                        self.memory.add_message("user", user_input)
                        self.memory.add_message("assistant", response)
                        if conv_id:
                            self.conversations.save_message(conv_id, "assistant", response)
                        await websocket.send_json({"type": "response", "message": response, "timestamp": datetime.now().isoformat()})
                        continue

                    # Send "thinking" indicator
                    await websocket.send_json({"type": "thinking"})

                    # ── Streaming path for simple chat (no skills needed) ──
                    # We attempt streaming first; if the response contains skill
                    # calls, we fall back to the full pipeline.
                    if not images:
                        streamed = await self._try_streaming_response(
                            websocket, session, user_input, conv_id
                        )
                        if streamed:
                            continue

                    # ── Full pipeline with skill execution (non-streaming) ──
                    result = await asyncio.to_thread(
                        self._process_input_safe, user_input, websocket, images, None, True
                    )

                    # Extract text and optional image data
                    if isinstance(result, dict):
                        response_text = result.get("message", "")
                        data_items = result.get("data", [])
                        gen_images = [d["image_b64"] for d in data_items if isinstance(d, dict) and "image_b64" in d]
                    else:
                        response_text = result
                        gen_images = []

                    # Send complete response immediately (no artificial streaming delay)
                    ws_payload = {
                        "type": "response",
                        "message": response_text,
                        "timestamp": datetime.now().isoformat(),
                    }
                    if gen_images:
                        ws_payload["images"] = gen_images
                    await websocket.send_json(ws_payload)

                    # Save assistant response to conversation
                    conv_id = self._session_conversations.get(session)
                    if conv_id:
                        self.conversations.save_message(conv_id, "assistant", response_text)

            except WebSocketDisconnect:
                # Curate memory before cleanup
                try:
                    self.curator.curate_session(self.memory.conversation_history)
                except Exception:
                    pass
                self._active_connections.remove(websocket)
                self._authenticated_ws.pop(websocket, None)
                logger.info("Client disconnected")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                if websocket in self._active_connections:
                    self._active_connections.remove(websocket)
                self._authenticated_ws.pop(websocket, None)

        @app.post("/api/clear")
        async def clear_memory(token: str = Depends(require_auth)):
            """Clear conversation history."""
            self.memory.clear_session()
            return {"status": "ok", "message": "Conversation cleared"}

        @app.get("/api/skills")
        async def list_skills(token: str = Depends(require_auth)):
            """List available skills."""
            return {
                "skills": {
                    name: {
                        "description": skill.description,
                        "parameters": skill.parameters,
                    }
                    for name, skill in self.skills.items()
                }
            }

        @app.get("/api/usage")
        async def get_usage(token: str = Depends(require_auth)):
            """Get token usage statistics."""
            return {
                "session": self.brain.usage_tracker.get_session_summary(),
                "daily": self.brain.usage_tracker.get_daily_summary(),
            }

        @app.get("/api/audit")
        async def audit_log(token: str = Depends(require_auth)):
            """View recent action audit log."""
            return {"log": self.safety.get_audit_log(last_n=50)}

        # ── API Key Management ──

        @app.post("/api/settings/apikey")
        async def set_api_key(request: Request, token: str = Depends(require_auth)):
            """Set an API key for a cloud provider and persist to .env."""
            body = await request.json()
            provider = body.get("provider", "")
            api_key = body.get("api_key", "")
            if not provider or not api_key:
                raise HTTPException(status_code=400, detail="provider and api_key required")
            self.brain.set_api_key(provider, api_key)
            # Persist to .env
            env_map = {
                "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
                "google": "GEMINI_API_KEY", "gemini": "GEMINI_API_KEY",
                "deepseek": "DEEPSEEK_API_KEY", "openrouter": "OPENROUTER_API_KEY",
                "mistral": "MISTRAL_API_KEY", "xai": "XAI_API_KEY",
                "perplexity": "PERPLEXITY_API_KEY", "huggingface": "HUGGINGFACE_API_KEY",
                "stability": "STABILITY_API_KEY", "elevenlabs": "WINSTON_ELEVENLABS_KEY",
            }
            env_key = env_map.get(provider)
            if env_key:
                save_env_value(env_key, api_key)
            return {"status": "ok", "provider": provider}

        @app.get("/api/settings/apikeys")
        async def get_api_keys(token: str = Depends(require_auth)):
            """Get which providers have API keys configured."""
            return {"api_keys": self.brain.get_api_keys()}

        @app.post("/api/settings/image_gen")
        async def set_image_gen(request: Request, token: str = Depends(require_auth)):
            """Set image generation provider and model."""
            body = await request.json()
            provider = body.get("provider", "pollinations")
            model = body.get("model", "dall-e-3")
            self.config.image_provider = provider
            self.config.image_model = model
            # Update skill defaults
            if "image_gen" in self.skills:
                self.skills["image_gen"].default_provider = provider
                self.skills["image_gen"].default_model = model
            return {"status": "ok", "provider": provider, "model": model}

        # ── Channel & Integration Settings (Web UI configurable) ──

        @app.get("/api/settings/channels")
        async def get_channel_settings(token: str = Depends(require_auth)):
            """Return current channel & integration config (tokens masked)."""
            def mask(val: str) -> str:
                if not val:
                    return ""
                if len(val) <= 8:
                    return "••••"
                return val[:4] + "•" * (len(val) - 8) + val[-4:]

            return {
                "telegram": {
                    "enabled": self.config.channels.telegram.enabled,
                    "bot_token": mask(self.config.channels.telegram.bot_token),
                    "has_token": bool(self.config.channels.telegram.bot_token),
                    "default_chat_id": self.config.channels.telegram.default_chat_id,
                },
                "discord": {
                    "enabled": self.config.channels.discord.enabled,
                    "bot_token": mask(self.config.channels.discord.bot_token),
                    "has_token": bool(self.config.channels.discord.bot_token),
                    "default_channel_id": self.config.channels.discord.default_channel_id,
                },
                "whatsapp": {
                    "enabled": self.config.channels.whatsapp.enabled,
                    "waha_url": self.config.channels.whatsapp.waha_url,
                    "has_api_key": bool(self.config.channels.whatsapp.waha_api_key),
                },
                "email": {
                    "smtp_server": self.config.email.smtp_server,
                    "email": self.config.email.email,
                    "has_password": bool(self.config.email.password),
                },
                "elevenlabs": {
                    "has_key": bool(self.config.tts.elevenlabs_api_key),
                    "voice_id": self.config.tts.elevenlabs_voice_id,
                },
                "ollama": {
                    "host": self.config.ollama.host,
                    "model": self.config.ollama.model,
                },
            }

        @app.post("/api/settings/channels")
        async def save_channel_settings(request: Request, token: str = Depends(require_auth)):
            """Save channel & integration settings. Persists to .env and updates live config."""
            body = await request.json()
            updated = []

            # ── Telegram ──
            tg = body.get("telegram")
            if tg:
                tok = tg.get("bot_token", "").strip()
                if tok:
                    save_env_value("WINSTON_TELEGRAM_TOKEN", tok)
                    self.config.channels.telegram.bot_token = tok
                    self.config.channels.telegram.enabled = True
                    updated.append("telegram")
                chat_id = tg.get("default_chat_id", "").strip()
                if chat_id:
                    self.config.channels.telegram.default_chat_id = chat_id

            # ── Discord ──
            dc = body.get("discord")
            if dc:
                tok = dc.get("bot_token", "").strip()
                if tok:
                    save_env_value("WINSTON_DISCORD_TOKEN", tok)
                    self.config.channels.discord.bot_token = tok
                    self.config.channels.discord.enabled = True
                    updated.append("discord")
                ch_id = dc.get("default_channel_id", "").strip()
                if ch_id:
                    self.config.channels.discord.default_channel_id = ch_id

            # ── WhatsApp ──
            wa = body.get("whatsapp")
            if wa:
                url = wa.get("waha_url", "").strip()
                if url:
                    save_env_value("WINSTON_WAHA_URL", url)
                    self.config.channels.whatsapp.waha_url = url
                api_key = wa.get("waha_api_key", "").strip()
                if api_key:
                    save_env_value("WINSTON_WAHA_API_KEY", api_key)
                    self.config.channels.whatsapp.waha_api_key = api_key
                    self.config.channels.whatsapp.enabled = True
                    updated.append("whatsapp")

            # ── Email ──
            em = body.get("email")
            if em:
                for field, env_key in [
                    ("smtp_server", "WINSTON_SMTP_SERVER"),
                    ("email", "WINSTON_EMAIL"),
                    ("password", "WINSTON_EMAIL_PASSWORD"),
                    ("imap_server", "WINSTON_IMAP_SERVER"),
                ]:
                    val = em.get(field, "").strip()
                    if val:
                        save_env_value(env_key, val)
                        setattr(self.config.email, field, val)
                        if field not in updated:
                            updated.append("email")

            # ── ElevenLabs TTS ──
            el = body.get("elevenlabs")
            if el:
                key = el.get("api_key", "").strip()
                if key:
                    save_env_value("WINSTON_ELEVENLABS_KEY", key)
                    self.config.tts.elevenlabs_api_key = key
                    self.config.tts.engine = "elevenlabs"
                    updated.append("elevenlabs")
                vid = el.get("voice_id", "").strip()
                if vid:
                    save_env_value("WINSTON_ELEVENLABS_VOICE", vid)
                    self.config.tts.elevenlabs_voice_id = vid

            # ── Ollama ──
            ol = body.get("ollama")
            if ol:
                host = ol.get("host", "").strip()
                if host:
                    save_env_value("OLLAMA_HOST", host)
                    self.config.ollama.host = host
                    updated.append("ollama")
                model = ol.get("model", "").strip()
                if model:
                    save_env_value("OLLAMA_MODEL", model)
                    self.config.ollama.model = model

            needs_restart = bool({"telegram", "discord", "whatsapp"} & set(updated))
            return {
                "status": "ok",
                "updated": updated,
                "restart_required": needs_restart,
                "message": "Settings saved to .env. " + (
                    "Restart Winston to activate channel changes." if needs_restart else "Applied immediately."
                ),
            }

        # ── Privacy / PII Settings ──

        @app.get("/api/settings/privacy")
        async def get_privacy_settings(token: str = Depends(require_auth)):
            """Return current PII redaction settings and stats."""
            guard = self.brain.pii_guard
            categories = guard.get_categories()
            return {
                "enabled": guard.enabled,
                "categories": categories,
                "stats": guard.get_stats(),
            }

        @app.post("/api/settings/privacy")
        async def save_privacy_settings(request: Request, token: str = Depends(require_auth)):
            """Update PII redaction settings."""
            body = await request.json()
            guard = self.brain.pii_guard

            # Master switch
            if "enabled" in body:
                enabled = bool(body["enabled"])
                guard.enabled = enabled
                self.config.privacy.pii_redaction = enabled
                save_env_value("WINSTON_PII_REDACTION", "true" if enabled else "false")

            # Per-category toggles
            cat_map = body.get("categories", {})
            config_field_map = {
                "email": "redact_emails",
                "phone": "redact_phones",
                "address_de": "redact_addresses",
                "address_us": "redact_addresses",
                "iban": "redact_financial",
                "credit_card": "redact_financial",
                "steuer_id": "redact_ids",
                "ssn": "redact_ids",
                "passport": "redact_ids",
            }
            for cat_name, enabled in cat_map.items():
                guard.set_pattern_enabled(cat_name, bool(enabled))
                cfg_field = config_field_map.get(cat_name)
                if cfg_field:
                    setattr(self.config.privacy, cfg_field, bool(enabled))

            # Reset stats if requested
            if body.get("reset_stats"):
                guard.reset_stats()

            return {
                "status": "ok",
                "enabled": guard.enabled,
                "categories": guard.get_categories(),
            }

        # ── Conversations ──

        @app.get("/api/conversations")
        async def list_conversations(token: str = Depends(require_auth)):
            """List saved conversations."""
            return {"conversations": self.conversations.list_conversations()}

        @app.get("/api/conversations/{conv_id}")
        async def get_conversation(conv_id: str, token: str = Depends(require_auth)):
            """Get a specific conversation."""
            conv = self.conversations.get_conversation(conv_id)
            if not conv:
                raise HTTPException(status_code=404, detail="Conversation not found")
            return conv

        @app.delete("/api/conversations/{conv_id}")
        async def delete_conversation(conv_id: str, token: str = Depends(require_auth)):
            """Delete a conversation."""
            self.conversations.delete_conversation(conv_id)
            return {"status": "ok"}

        @app.put("/api/conversations/{conv_id}")
        async def rename_conversation(conv_id: str, request: Request, token: str = Depends(require_auth)):
            """Rename a conversation."""
            body = await request.json()
            title = body.get("title", "")
            if title:
                self.conversations.rename_conversation(conv_id, title)
            return {"status": "ok"}

        @app.post("/api/conversations/new")
        async def new_conversation(request: Request, token: str = Depends(require_auth)):
            """Start a new conversation, clearing current session memory."""
            self.memory.clear_session()
            # Remove session conversation mapping
            session_token = request.headers.get("Authorization", "").replace("Bearer ", "")
            self._session_conversations.pop(session_token, None)
            conv_id = self.conversations.create_conversation()
            self._session_conversations[session_token] = conv_id
            return {"status": "ok", "conversation_id": conv_id}

        @app.post("/api/conversations/{conv_id}/load")
        async def load_conversation(conv_id: str, request: Request, token: str = Depends(require_auth)):
            """Load a conversation back into memory."""
            conv = self.conversations.get_conversation(conv_id)
            if not conv:
                raise HTTPException(status_code=404, detail="Conversation not found")
            self.memory.clear_session()
            for msg in conv.get("messages", []):
                self.memory.add_message(msg["role"], msg["content"])
            session_token = request.headers.get("Authorization", "").replace("Bearer ", "")
            self._session_conversations[session_token] = conv_id
            return {"status": "ok", "messages": conv.get("messages", [])}

        # ── Routines ──

        @app.get("/api/routines")
        async def list_routines(token: str = Depends(require_auth)):
            """List available routines."""
            return {"routines": self.routines.list_routines()}

        @app.post("/api/routines/{routine_id}/run")
        async def run_routine(routine_id: str, token: str = Depends(require_auth)):
            """Execute a routine and return results."""
            results = self.routines.execute_routine(routine_id)
            if not results:
                raise HTTPException(status_code=404, detail="Routine not found")
            results_text = "\n".join(f"- {r['message']}" for r in results if r)
            summary = self.brain.think(
                f"I just ran a routine. Here are the results:\n{results_text}\n\n"
                f"Summarize these results naturally for the user.",
                conversation_history=[],
            )
            summary = self.brain.strip_skill_blocks(summary) if '"skill"' in summary else summary
            return {"status": "ok", "summary": summary, "results": [r['message'] for r in results if r]}

        @app.post("/api/routines")
        async def create_routine(request: Request, token: str = Depends(require_auth)):
            """Create a custom routine."""
            body = await request.json()
            rid = self.routines.create_custom(
                name=body.get("name", "Custom"),
                triggers=body.get("triggers", []),
                steps=body.get("steps", []),
            )
            return {"status": "ok", "id": rid}

        @app.delete("/api/routines/{routine_id}")
        async def delete_routine(routine_id: str, token: str = Depends(require_auth)):
            """Delete a custom routine."""
            self.routines.delete_routine(routine_id)
            return {"status": "ok"}

        # ── Identity ──

        @app.get("/api/identity")
        async def list_identity_files(token: str = Depends(require_auth)):
            """List identity files."""
            return {
                "files": self.identity.list_files(),
                "journals": self.identity.list_journals(10),
            }

        @app.get("/api/identity/{filename}")
        async def get_identity_file(filename: str, token: str = Depends(require_auth)):
            """Read an identity file."""
            content = self.identity.read_file(filename)
            return {"filename": filename, "content": content}

        @app.put("/api/identity/{filename}")
        async def update_identity_file(filename: str, request: Request, token: str = Depends(require_auth)):
            """Update an identity file."""
            body = await request.json()
            self.identity.update_file(filename, body.get("content", ""))
            return {"status": "ok"}

        # ── Scheduler ──

        @app.get("/api/schedules")
        async def list_schedules(token: str = Depends(require_auth)):
            """List all scheduled tasks."""
            return {"tasks": self.scheduler.list_tasks()}

        @app.post("/api/schedules")
        async def create_schedule(request: Request, token: str = Depends(require_auth)):
            """Create a new scheduled task."""
            body = await request.json()
            task_id = self.scheduler.create_task(
                name=body.get("name", "Custom Task"),
                task_type=body.get("task_type", "interval"),
                schedule=body.get("schedule", {"minutes": 60}),
                action=body.get("action", {}),
                deliver_to=body.get("deliver_to", []),
                description=body.get("description", ""),
            )
            return {"status": "ok", "task_id": task_id}

        @app.delete("/api/schedules/{task_id}")
        async def delete_schedule(task_id: str, token: str = Depends(require_auth)):
            """Delete a scheduled task."""
            self.scheduler.delete_task(task_id)
            return {"status": "ok"}

        @app.post("/api/schedules/{task_id}/toggle")
        async def toggle_schedule(task_id: str, request: Request, token: str = Depends(require_auth)):
            """Enable or disable a scheduled task."""
            body = await request.json()
            if body.get("enabled", True):
                self.scheduler.enable_task(task_id)
            else:
                self.scheduler.disable_task(task_id)
            return {"status": "ok"}

        @app.post("/api/schedules/{task_id}/trigger")
        async def trigger_schedule(task_id: str, token: str = Depends(require_auth)):
            """Manually trigger a scheduled task."""
            result = await self.scheduler.trigger_task(task_id)
            return {"status": "ok", "result": result}

        # ── Webhook (validates via X-Webhook-Secret header) ──

        @app.post("/api/webhook/{task_id}")
        async def webhook_trigger(task_id: str, request: Request):
            """External webhook trigger for scheduled tasks."""
            secret = request.headers.get("X-Webhook-Secret", "")
            expected = self.config.server.webhook_secret
            if not expected or secret != expected:
                raise HTTPException(status_code=403, detail="Invalid webhook secret")
            result = await self.scheduler.trigger_task(task_id)
            return {"status": "ok", "result": result}

        # ── Channels ──

        @app.get("/api/channels")
        async def list_channels(token: str = Depends(require_auth)):
            """List active messaging channels."""
            return {"channels": self.channel_manager.list_channels()}

    def _process_input(self, user_input: str, images: list[str] = None, channel=None, return_dict: bool = False) -> Union[str, dict]:
        """Process user input (for REST API, no confirmation flow)."""
        sanitized = self.safety.sanitize_input(user_input)

        # Detect autonomous/careful mode prefixes
        override, sanitized = parse_override(sanitized)
        if override != RiskOverride.NONE:
            logger.info(f"{override.name} mode activated for REST request")

        # ── Shopping intent detection (same as main.py) ──
        shop_key = is_shopping_intent(sanitized)
        if shop_key and "browser" in self.skills:
            import os as _os
            from winston.skills.browser_skill import BrowserSkill
            from winston.core.browser_agent import InteractiveBrowserAgent
            profile_dir, shop_url, shop_name = SHOP_PROFILES[shop_key]
            profile_dir = _os.path.expanduser(profile_dir)
            has_profile = _os.path.isdir(profile_dir) and _os.listdir(profile_dir)
            if not has_profile:
                resp = (
                    f"Ich kann bei {shop_name} bestellen, aber du musst dich zuerst einloggen.\n"
                    f"Führe einmal aus: python tests/flink_login_setup.py"
                )
                self.memory.add_message("user", sanitized)
                self.memory.add_message("assistant", resp)
                if return_dict:
                    return {"message": resp, "skill_results": [], "data": []}
                return resp

            logger.info(f"Shopping intent detected → {shop_name}")
            browser_with_profile = BrowserSkill(headless=False, user_data_dir=profile_dir)
            task = (
                f"Du bist auf {shop_url} und eingeloggt. "
                f"Adresse und Liefergebiet sind gespeichert.\n\n"
                f"AUFTRAG: {sanitized}\n\n"
                f"REGELN: Suchfunktion nutzen, click_ref für Buttons, "
                f"NIEMALS open_page zu /cart/. Am Ende Warenkorb-Icon klicken, "
                f"Screenshot + Bericht."
            )
            self.memory.add_message("user", sanitized)
            browser_with_profile._ensure_browser()
            browser_with_profile._page.goto(shop_url, timeout=30000)
            import time as _time
            _time.sleep(2)
            agent = InteractiveBrowserAgent(self.brain, self.safety, browser_with_profile)
            resp = agent.execute_task(
                user_input=task, override=RiskOverride.AUTONOMOUS, channel=channel,
            )
            try:
                browser_with_profile.cleanup()
            except Exception:
                pass
            self.memory.add_message("assistant", resp)
            if return_dict:
                return {"message": resp, "skill_results": [], "data": []}
            return resp

        # Auto-capture memorable information
        self.memory.auto_capture(sanitized)

        self.memory.add_message("user", sanitized)

        # Compact conversation if approaching context window limit
        compact_memory(self.memory, self.brain, self.config.ollama.context_window)

        context = self.memory.get_context_messages()

        # Recall relevant memories for context injection
        memory_context = self.memory.recall_relevant(sanitized)
        enriched_input = f"{memory_context}\n\n{sanitized}" if memory_context else sanitized

        response = self.brain.think(enriched_input, conversation_history=context, images=images)

        skill_calls = self.brain.parse_skill_calls(response)
        skill_results = []

        # Fallback chain: web_fetch (URL) → web_search (keywords) → browser (intent)
        if not skill_calls:
            skill_calls = detect_fallback_calls(sanitized, response, self.skills, include_browser=True)

        if skill_calls:
            # INTERACTIVE BROWSER AGENT HANDOFF (also for REST with mach: prefix)
            if skill_calls[0].get("skill") == "browser" and override != RiskOverride.NONE:
                logger.info("Routing browser task from REST to InteractiveBrowserAgent")
                from winston.core.browser_agent import InteractiveBrowserAgent

                if "browser" not in self.skills:
                    resp = "Error: Browser skill is not currently loaded."
                    self.memory.add_message("assistant", resp)
                    return resp

                agent = InteractiveBrowserAgent(self.brain, self.safety, self.skills["browser"])
                routed_resp = agent.execute_task(
                    user_input=sanitized,
                    override=override,
                )
                self.memory.add_message("assistant", routed_resp)
                if return_dict:
                    return {"message": routed_resp, "skill_results": [], "data": []}
                return routed_resp

            for call in skill_calls:
                skill_name = call.get("skill", "")
                params = call.get("parameters", {})
                action_req = self.safety.request_action(skill_name, params, override=override)

                if action_req.risk_level == RiskLevel.BLOCKED:
                    continue

                # Auto-approve SAFE, LOW, and MEDIUM in web/REST mode
                if not action_req.approved:
                    if override == RiskOverride.AUTONOMOUS or action_req.risk_level == RiskLevel.MEDIUM:
                        self.safety.approve_action(action_req.id)
                        action_req.approved = True
                    else:
                        # HIGH risk: skip in REST mode (unless autonomous)
                        self.safety.deny_action(action_req.id)
                        continue

                if action_req.approved and skill_name in self.skills:
                    try:
                        # Restore PII placeholders before local skill execution
                        params = self.brain.pii_guard.restore_params(params)
                        result = self.skills[skill_name].execute(**params)
                        skill_results.append(result)

                        # Stop sequential execution if a step fails
                        if not result.success:
                            logger.warning(f"Aborting REST sequential execution due to failure in {skill_name}")
                            break
                    except Exception as e:
                        skill_results.append(SkillResult(success=False, message=str(e)))
                        break

            if skill_results:
                response = refine_response(self.brain, sanitized, skill_results, context)
            else:
                # Skill calls found but none executed — strip JSON and return what's left
                response = self.brain.strip_skill_blocks(response)
                if not response:
                    response = "I tried to process that, but couldn't complete the action."

        response = finalize_response(response, self.brain, self.safety)
        # Restore PII placeholders so the user sees real data in replies
        response = self.brain.pii_guard.restore(response)
        self.memory.add_message("assistant", response)

        if return_dict:
            return {
                "message": response,
                "skill_results": [r.message for r in skill_results],
                "data": [r.data for r in skill_results if r.data]
            }
        return response

    async def _try_streaming_response(self, websocket, session: str, user_input: str, conv_id: Optional[str] = None) -> bool:
        """Stream a response token-by-token over WebSocket.

        Returns True if the response was fully streamed (no skill calls detected).
        Returns False if skill calls were detected, so the caller should fall back
        to the full (non-streaming) pipeline.
        """
        sanitized = self.safety.sanitize_input(user_input)
        override, sanitized = parse_override(sanitized)

        self.memory.auto_capture(sanitized)
        self.memory.add_message("user", sanitized)
        compact_memory(self.memory, self.brain, self.config.ollama.context_window)

        context = self.memory.get_context_messages()
        memory_context = self.memory.recall_relevant(sanitized)
        enriched_input = f"{memory_context}\n\n{sanitized}" if memory_context else sanitized

        full_response = ""
        try:
            async for token in self.brain.think_stream_async(enriched_input, conversation_history=context):
                full_response += token

                # If we detect a skill/JSON block starting, abort streaming
                # and let the full pipeline handle it
                if '```json' in full_response or '"skill"' in full_response:
                    # Undo the user message we already added (will be re-added by _process_input_safe)
                    if self.memory.conversation_history and self.memory.conversation_history[-1].get("role") == "user":
                        self.memory.conversation_history.pop()
                    return False

                # Send each token to the frontend
                await websocket.send_json({"type": "stream", "token": token})

        except Exception as e:
            logger.error(f"Streaming failed: {e}")
            if self.memory.conversation_history and self.memory.conversation_history[-1].get("role") == "user":
                self.memory.conversation_history.pop()
            return False

        # Streaming complete — finalize
        full_response = finalize_response(full_response, self.brain, self.safety)

        # Signal end of stream
        await websocket.send_json({
            "type": "stream_end",
            "message": full_response,
            "timestamp": datetime.now().isoformat(),
        })

        self.memory.add_message("assistant", full_response)
        if conv_id:
            self.conversations.save_message(conv_id, "assistant", full_response)

        return True

    def _process_input_safe(self, user_input: str, websocket: WebSocket = None, images: list[str] = None, channel=None, return_dict: bool = False) -> Union[str, dict]:
        """Process input with full safety checks and WebSocket confirmation."""
        sanitized = self.safety.sanitize_input(user_input)

        # Detect autonomous/careful mode prefixes
        override, sanitized = parse_override(sanitized)
        if override != RiskOverride.NONE:
            logger.info(f"{override.name} mode activated for WS request")

        # Auto-capture memorable information
        self.memory.auto_capture(sanitized)

        self.memory.add_message("user", sanitized)

        # Compact conversation if approaching context window limit
        compact_memory(self.memory, self.brain, self.config.ollama.context_window)

        context = self.memory.get_context_messages()

        # Recall relevant memories for context injection
        memory_context = self.memory.recall_relevant(sanitized)
        enriched_input = f"{memory_context}\n\n{sanitized}" if memory_context else sanitized

        response = self.brain.think(enriched_input, conversation_history=context, images=images)

        skill_calls = self.brain.parse_skill_calls(response)
        skill_results = []

        # Fallback chain: web_fetch (URL) → web_search (keywords) → browser (intent)
        if not skill_calls:
            skill_calls = detect_fallback_calls(sanitized, response, self.skills, include_browser=True)

        if skill_calls:
            # INTERACTIVE BROWSER AGENT HANDOFF
            if skill_calls[0].get("skill") == "browser" and override != RiskOverride.NONE:
                logger.info("Routing complex browser task from server to InteractiveBrowserAgent")
                from winston.core.browser_agent import InteractiveBrowserAgent
                
                if "browser" not in self.skills:
                    resp = "Error: Browser skill is not currently loaded."
                    self.memory.add_message("assistant", resp)
                    return resp

                def ws_confirm(req, ws):
                    if not ws: return False
                    import asyncio
                    loop = asyncio.get_event_loop()
                    future = loop.create_future()
                    self._pending_ws_confirmations[req.id] = future
                    try:
                        confirm_msg = {
                            "type": "action_required", "action_id": req.id,
                            "skill": "browser", "description": req.description,
                            "risk": req.risk_level.value, "screenshot": req.screenshot_path
                        }
                        asyncio.run_coroutine_threadsafe(ws.send_json(confirm_msg), loop)
                        return future.result(timeout=600)
                    except Exception as e:
                        logger.error(f"WebSocket confirmation timed out: {e}")
                        return False
                    finally:
                        self._pending_ws_confirmations.pop(req.id, None)

                agent = InteractiveBrowserAgent(self.brain, self.safety, self.skills["browser"])
                routed_resp = agent.execute_task(
                    user_input=sanitized,
                    override=override,
                    channel=websocket,
                    confirm_callback=ws_confirm
                )
                self.memory.add_message("assistant", routed_resp)
                return routed_resp

            skill_results = []
            blocked = []

            for call in skill_calls:
                skill_name = call.get("skill", "")
                params = call.get("parameters", {})
                action_req = self.safety.request_action(skill_name, params, override=override)

                if action_req.risk_level == RiskLevel.BLOCKED:
                    blocked.append(action_req.description)
                    continue

                if not action_req.approved:
                    # Capture screenshot for CAREFUL mode browser actions
                    if override == RiskOverride.CAREFUL and skill_name == "browser":
                        try:
                            # Use screenshot_page action
                            shot_res = self.skills["browser"].execute(action="screenshot_page")
                            if shot_res.success:
                                action_req.screenshot_path = shot_res.data.get("path")
                        except Exception as e:
                            logger.warning(f"Failed to capture screenshot for confirmation: {e}")

                    # Auto-approve safe skills unless in CAREFUL mode
                    if override != RiskOverride.CAREFUL and skill_name in AUTO_APPROVE_SKILLS:
                        self.safety.approve_action(action_req.id)
                        action_req.approved = True
                    elif websocket:
                        # Request confirmation via WebSocket
                        loop = asyncio.get_event_loop()
                        future = loop.create_future()
                        self._pending_ws_confirmations[action_req.id] = future
                        
                        try:
                            # Send confirmation request to frontend
                            confirm_msg = {
                                "type": "action_required",
                                "action_id": action_req.id,
                                "skill": skill_name,
                                "description": action_req.description,
                                "risk": action_req.risk_level.value,
                                "screenshot": action_req.screenshot_path
                            }
                            asyncio.run_coroutine_threadsafe(websocket.send_json(confirm_msg), loop)
                            
                            # Wait for result (timeout 10 minutes)
                            try:
                                # We are in a thread, so we can't await, but we can't .result() easily
                                # without blocking the whole thread pool if we have many.
                                # But uvicorn's threadpool is usually fine for a few clients.
                                approved = future.result(timeout=600)
                                action_req.approved = approved
                            except Exception as e:
                                logger.error(f"WebSocket confirmation timed out: {e}")
                                action_req.approved = False
                        finally:
                            self._pending_ws_confirmations.pop(action_req.id, None)
                    else:
                        # No websocket, HIGH risk: deny
                        blocked.append(f"(Needs approval) {action_req.description}")
                        self.safety.deny_action(action_req.id)
                        continue

                if skill_name in self.skills:
                    try:
                        # Restore PII placeholders before local skill execution
                        params = self.brain.pii_guard.restore_params(params)
                        result = self.skills[skill_name].execute(**params)
                        skill_results.append(result)
                        
                        # Stop if a step fails
                        if not result.success:
                            logger.warning(f"Aborting server sequential execution due to failure in {skill_name}")
                            break
                    except Exception as e:
                        skill_results.append(SkillResult(success=False, message=str(e)))
                        break

            if skill_results:
                response = refine_response(self.brain, sanitized, skill_results, context)
            elif blocked:
                response = (
                    "I wanted to perform that action, but it requires approval first. "
                    "The following were blocked by safety controls: "
                    + "; ".join(blocked)
                    + ". You can re-try from the terminal (--mode text) where confirmation prompts are supported."
                )
            else:
                # Skill calls detected but none executed or blocked
                response = self.brain.strip_skill_blocks(response)
                if not response:
                    response = "I tried to process that, but couldn't complete the action."

        response = finalize_response(response, self.brain, self.safety)
        # Restore PII placeholders so the user sees real data in replies
        response = self.brain.pii_guard.restore(response)
        self.memory.add_message("assistant", response)
        
        if return_dict:
            return {
                "message": response,
                "skill_results": [r.message for r in skill_results],
                "data": [r.data for r in skill_results if r.data]
            }
        return response

    def run(self, host: str = "0.0.0.0", port: int = 8000):
        """Start the web server with channels and scheduler."""
        import socket
        local_ip = socket.gethostbyname(socket.gethostname())
        token = self.auth.get_access_token()
        pin = self.auth.get_display_pin()

        print(f"\n  W.I.N.S.T.O.N. Web Server")
        print(f"  ──────────────────────────────")
        print(f"  Local:   http://localhost:{port}")
        print(f"  Network: http://{local_ip}:{port}")
        print(f"  Model:   {self.config.ollama.model}")
        print(f"  ──────────────────────────────")

        # Show channel status
        channels = self.channel_manager.list_channels()
        if channels:
            print(f"  Channels: {', '.join(c['name'] for c in channels)}")
        else:
            print(f"  Channels: none (set WINSTON_TELEGRAM_TOKEN or WINSTON_DISCORD_TOKEN)")

        # Show scheduler status
        enabled_tasks = sum(1 for t in self.scheduler.list_tasks() if t["enabled"])
        print(f"  Scheduler: {enabled_tasks} active tasks")

        print(f"  ──────────────────────────────")
        print(f"  SECURITY:")
        if pin:
            print(f"  PIN:     {pin}")
        else:
            print(f"  Token:   {token}")
        print(f"  Enter this on the login screen")
        print(f"  from your phone to connect.")
        print(f"  ──────────────────────────────\n")

        asyncio.run(self._run_all(host, port))

    async def _run_all(self, host: str, port: int):
        """Run web server, channels, and scheduler concurrently."""
        config = uvicorn.Config(self.app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)

        # Wire up reminder notifications to messaging channels (Telegram etc.)
        # fire() runs in a threading.Timer thread, so we bridge sync → async here.
        loop = asyncio.get_running_loop()
        channel_mgr = self.channel_manager
        if "calendar" in self.skills:
            def _reminder_notify(msg: str):
                asyncio.run_coroutine_threadsafe(channel_mgr.broadcast(msg), loop)
            self.skills["calendar"]._notify_fn = _reminder_notify

        # Start scheduler
        await self.scheduler.start()

        # Rate limiter cleanup task (every 5 minutes)
        async def _rate_limit_cleanup():
            while True:
                await asyncio.sleep(300)
                self._rate_limiter.cleanup()
                self._ws_rate_limiter.cleanup()

        asyncio.create_task(_rate_limit_cleanup())

        # Start channel health monitor
        if self.channel_manager._channels:
            asyncio.create_task(self.health_monitor.start())

        # Build task list
        tasks = [server.serve()]

        # Start channels if any are registered
        if self.channel_manager._channels:
            tasks.append(self.channel_manager.start_all())

        try:
            await asyncio.gather(*tasks)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            # Cleanup
            await self.health_monitor.stop()
            await self.scheduler.stop()
            await self.channel_manager.stop_all()
            # Save usage data
            try:
                self.brain.usage_tracker.save()
            except Exception:
                pass
            # Final memory curation
            try:
                self.curator.curate_session(self.memory.conversation_history)
            except Exception:
                pass


def start_server(config: WinstonConfig = None, host: str = "0.0.0.0", port: int = 8000):
    """Start the W.I.N.S.T.O.N. web server."""
    server = WinstonServer(config)
    server.run(host=host, port=port)
