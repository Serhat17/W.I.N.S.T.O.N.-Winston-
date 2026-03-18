"""
W.I.N.S.T.O.N. Main Orchestrator
Ties together all modules: Brain, Listener, Speaker, Memory, and Skills.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import os

# Disable parallelism warnings from tokenizers to avoid deadlocks on fork()
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import threading
from pathlib import Path
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from winston.channels.base import BaseChannel

from winston.config import WinstonConfig, load_config
from winston.core.brain import Brain
from winston.core.listener import Listener
from winston.core.observer import Observer
from winston.core.speaker import Speaker
from winston.core.memory import Memory
from winston.core.safety import SafetyGuard, RiskLevel, RiskOverride
from winston.core.pipeline import (
    parse_override, detect_fallback_calls, refine_response,
    finalize_response, compact_memory, AUTO_APPROVE_SKILLS,
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
from winston.skills.web_fetch_skill import WebFetchSkill
from winston.skills.browser_skill import BrowserSkill
from winston.skills.knowledge_base_skill import KnowledgeBaseSkill
from winston.skills.shopping_skill import ShoppingSkill
from winston.skills.image_gen_skill import ImageGenerationSkill
from winston.core.identity import IdentityManager, MemoryCurator
from winston.core.agent_router import AgentRouter
from winston.utils.helpers import (
    setup_logging,
    print_banner,
    print_winston,
    print_user,
    print_system,
    print_error,
    print_success,
    print_skill_result,
    get_greeting,
)

logger = logging.getLogger("winston.main")


class Winston:
    """
    Main W.I.N.S.T.O.N. orchestrator.
    Coordinates all modules and manages the conversation loop.
    """

    def __init__(self, config: WinstonConfig = None):
        self.config = config or load_config()
        self._running = False
        self._skills = {}
        self._channels: List = []
        self._channel_thread: Optional[threading.Thread] = None
        self._channel_loop: Optional[asyncio.AbstractEventLoop] = None

        # Set up logging
        setup_logging(self.config.log_file, self.config.debug)

        # Initialize safety guard
        self.safety = SafetyGuard(require_confirmation=True)
        print_success("Safety guardrails active")

        # Initialize core modules
        print_system("Initializing W.I.N.S.T.O.N. core systems...")

        # Initialize identity system
        self.identity = IdentityManager()
        print_success("Identity system loaded")

        # Initialize multi-agent routing
        config_dir = str(Path(__file__).parent.parent / "config")
        self.agent_router = AgentRouter(config_dir=config_dir)
        print_success(f"Agent router loaded ({len(self.agent_router.list_agents())} agents)")

        self.brain = Brain(self.config, identity=self.identity)
        print_success("Brain (LLM) online")

        self.curator = MemoryCurator(self.identity, self.brain)

        self.memory = Memory(self.config.memory)
        print_success("Memory systems online")

        self.speaker = Speaker(self.config.tts)
        print_success("Speech synthesis ready")

        if self.config.input_mode in ("voice", "hybrid"):
            self.listener = Listener(self.config.whisper, self.config.wake_word)
            print_success("Voice recognition ready")
        else:
            self.listener = None

        # Initialize Observer Mode (requires listener for transcription)
        if self.listener:
            self.observer = Observer(self.config, self.listener, callback=self._observer_callback)
            print_success("Observer logic ready (inactive)")
        else:
            self.observer = None

        # Register skills
        self._register_skills()

        # Register signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)

        # Start messaging channels (Telegram, etc.) in background
        self._start_channels()

    def _register_skills(self):
        """Register all available skills."""
        print_system("Loading skills...")

        self._scheduler_skill = SchedulerSkill()

        # Audio analysis skill with transcriber + brain injection
        audio_skill = AudioAnalysisSkill(
            transcribe_fn=self.listener.transcribe_file if self.listener else None,
            brain=self.brain,
        )

        self._price_monitor_skill = PriceMonitorSkill()

        skills = [
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
            self._scheduler_skill,
            audio_skill,
            TravelSkill(self.config.amadeus),
            GoogleCalendarSkill(self.config.google_calendar),
            self._price_monitor_skill,
            WebFetchSkill(),
            BrowserSkill(),
            KnowledgeBaseSkill(),
            ImageGenerationSkill(self.config),
        ]

        # Wire ShoppingSkill with reference to BrowserSkill for profile management
        browser_skill = next(s for s in skills if s.name == "browser")
        skills.append(ShoppingSkill(browser_skill=browser_skill))

        for skill in skills:
            self._skills[skill.name] = skill
            print_success(f"  Skill loaded: {skill.name}")

        # Tell the brain about available skills
        self.brain.register_skills(self._skills)
        print_success(f"Total skills loaded: {len(self._skills)}")

    def _start_channels(self):
        """Start messaging channels (Telegram, Discord, etc.) and the scheduler in a background thread."""
        from winston.channels.telegram_channel import TelegramChannel
        from winston.core.scheduler import Scheduler
        from winston.core.routines import RoutineManager

        tg_cfg = self.config.channels.telegram
        self._telegram_channel = None
        if tg_cfg.enabled and tg_cfg.bot_token:
            channel = TelegramChannel(
                config={
                    "bot_token": tg_cfg.bot_token,
                    "allowed_users": tg_cfg.allowed_users,
                    "default_chat_id": tg_cfg.default_chat_id,
                },
                process_fn=self.process_input,
            )
            # Inject ElevenLabs config for Telegram voice messages
            channel._el_api_key = self.config.tts.elevenlabs_api_key
            channel._el_voice_id = self.config.tts.elevenlabs_voice_id
            channel._el_model = self.config.tts.elevenlabs_model
            
            # Inject transcriber callback for voice message parsing
            if self.listener:
                channel._transcribe_callback = self.listener.transcribe_file
                
            self._channels.append(channel)
            self._telegram_channel = channel
            print_success("Telegram channel enabled")

        if not self._channels:
            return

        # Build a minimal broadcaster that the scheduler can use to push messages
        telegram_ch = self._telegram_channel

        class _DirectBroadcaster:
            async def broadcast(self, message: str, targets=None):
                if telegram_ch and telegram_ch._default_chat_id:
                    await telegram_ch.send_message(telegram_ch._default_chat_id, message)
                else:
                    logger.warning(
                        "Scheduler broadcast dropped — no Telegram chat ID yet. "
                        "Send any message to the bot first so it learns your chat ID."
                    )

        # Build routines manager so the good_morning routine is available
        routines = RoutineManager(self._skills)

        # Build scheduler wired to skills, routines, brain, and broadcaster
        self._scheduler = Scheduler(
            skills=self._skills,
            routines=routines,
            brain=self.brain,
            channel_manager=_DirectBroadcaster(),
        )
        # Let the scheduler skill know about the real scheduler
        if "scheduler" in self._skills:
            self._skills["scheduler"].scheduler = self._scheduler
            self._skills["scheduler"].routines = routines

        # Wire scheduler + channels to the price monitor skill
        broadcaster = _DirectBroadcaster()
        if "price_monitor" in self._skills:
            self._skills["price_monitor"].scheduler = self._scheduler
            self._skills["price_monitor"].channel_manager = broadcaster

        # Monitor engine for background price checks
        if "price_monitor" in self._skills:
            from winston.core.monitor_engine import MonitorEngine
            self._monitor_engine = MonitorEngine(
                price_monitor_skill=self._skills["price_monitor"],
                channel_manager=broadcaster,
                brain=self.brain,
            )
            self._scheduler.monitor_engine = self._monitor_engine

        async def main_async():
            # Wire up reminder notifications to messaging channels
            if "calendar" in self._skills and self._telegram_channel:
                loop = asyncio.get_running_loop()
                tg_ch = self._telegram_channel
                def _reminder_notify(msg: str):
                    if tg_ch._default_chat_id:
                        asyncio.run_coroutine_threadsafe(
                            tg_ch.send_message(tg_ch._default_chat_id, msg), loop
                        )
                self._skills["calendar"]._notify_fn = _reminder_notify

            await self._scheduler.start()
            await asyncio.gather(*[ch.start() for ch in self._channels])

        def run_channels():
            self._channel_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._channel_loop)
            while True:
                try:
                    self._channel_loop.run_until_complete(main_async())
                    break  # clean exit
                except Exception as e:
                    logger.error(f"Channel/scheduler error: {e} — restarting in 10s")
                    import time
                    time.sleep(10)
            self._channel_loop.close()

        self._channel_thread = threading.Thread(target=run_channels, daemon=True, name="winston-channels")
        self._channel_thread.start()
        logger.info("Messaging channels and scheduler started in background")

    def _stop_channels(self):
        """Stop all messaging channels."""
        if self._channel_loop and not self._channel_loop.is_closed():
            for ch in self._channels:
                asyncio.run_coroutine_threadsafe(ch.stop(), self._channel_loop)

    def _signal_handler(self, signum, frame):
        """Handle Ctrl+C gracefully."""
        print("\n")
        # Curate session memory before shutting down
        try:
            self.curator.curate_session(self.memory.conversation_history)
        except Exception as e:
            logger.warning(f"Memory curation on shutdown failed: {e}")

        self._stop_channels()
        print_winston(f"Shutting down. Goodbye, {self.config.user_name}.")
        self.speaker.speak(f"Goodbye, {self.config.user_name}.")
        self._running = False
        self.brain.close()
        sys.exit(0)

    def process_input(
        self, 
        user_input: str, 
        channel: Optional[BaseChannel] = None,
        images: Optional[List[str]] = None
    ) -> str:
        """
        Process user input through the full pipeline.
        
        Args:
            user_input: The user's text input
            channel: The channel instance that received the input
            images: Optional list of base64 encoded images

        Returns:
            The assistant's response
        """
        # Handle special commands
        special = self._handle_special_commands(user_input)
        if special:
            return special

        # Sanitize input against prompt injection
        sanitized = self.safety.sanitize_input(user_input)

        # Detect autonomous/careful mode prefixes
        override, sanitized = parse_override(sanitized)
        if override != RiskOverride.NONE:
            logger.info(f"{override.name} mode activated for this request")

        # Add user message to memory
        self.memory.add_message("user", sanitized)

        # Compact conversation if approaching context window limit
        compact_memory(self.memory, self.brain, self.config.ollama.context_window)

        # Get conversation context
        context = self.memory.get_context_messages()
        
        # Think about the response
        response = self.brain.think(sanitized, conversation_history=context, images=images)

        # Check for skill invocations in the response
        skill_calls = self.brain.parse_skill_calls(response)

        # --- Fallback chain: web_fetch (URL) → web_search (keywords) → browser (intent) ---
        if not skill_calls:
            skill_calls = detect_fallback_calls(sanitized, response, self._skills, include_browser=True)

        if skill_calls:
            # INTERACTIVE BROWSER AGENT HANDOFF
            # If the user used 'mach / mach vorsichtig' AND the first intended skill is the browser,
            # we hand off the entire task to the InteractiveBrowserAgent for iterative execution.
            if skill_calls[0].get("skill") == "browser" and override != RiskOverride.NONE:
                logger.info("Routing complex browser task to InteractiveBrowserAgent")
                from winston.core.browser_agent import InteractiveBrowserAgent
                
                if "browser" not in self._skills:
                    response = "Error: Browser skill is not currently loaded."
                    self.memory.add_message("assistant", response)
                    return response
                    
                agent = InteractiveBrowserAgent(self.brain, self.safety, self._skills["browser"])
                response = agent.execute_task(
                    user_input=sanitized, 
                    override=override, 
                    channel=channel, 
                    confirm_callback=self._confirm_action
                )
                
                self.memory.add_message("assistant", response)
                return response

            # Standard Single-Shot Execution Flow
            # Safety-check each skill call
            skill_results = []
            blocked_actions = []

            for call in skill_calls:
                skill_name = call.get("skill", "")
                parameters = call.get("parameters", {})

                # Run through safety guard
                action_req = self.safety.request_action(skill_name, parameters, override=override)

                if action_req.risk_level == RiskLevel.BLOCKED:
                    blocked_actions.append(action_req.description)
                    continue

                if not action_req.approved:
                    # If CAREFUL mode and it's a browser action with a page open, 
                    # take a screenshot for visual confirmation.
                    if override == RiskOverride.CAREFUL and skill_name == "browser":
                        try:
                            logger.info("CAREFUL mode: capturing screenshot before confirmation")
                            shot_res = self._skills["browser"].execute(action="screenshot_page")
                            if shot_res.success:
                                action_req.screenshot_path = shot_res.data.get("path")
                        except Exception as e:
                            logger.warning(f"Failed to capture careful-mode screenshot: {e}")

                    # Auto-approve read-only / safe skills unless in CAREFUL mode
                    if override != RiskOverride.CAREFUL and skill_name in AUTO_APPROVE_SKILLS:
                        self.safety.approve_action(action_req.id)
                    else:
                        confirmed = self._confirm_action(action_req, channel=channel)
                        if not confirmed:
                            blocked_actions.append(f"(Denied) {action_req.description}")
                            continue

                # Execute approved action
                result = self._execute_skill(skill_name, parameters)
                if result:
                    skill_results.append(result)
                    
                    # CRITICAL: Stop sequential execution if a step fails
                    if not result.success:
                        logger.warning(f"Aborting sequential execution due to failure in {skill_name}")
                        break

            # Build response from results
            if blocked_actions:
                block_msg = "Blocked actions:\n" + "\n".join(f"  - {a}" for a in blocked_actions)
                print_system(block_msg)

            if skill_results:
                response = refine_response(self.brain, sanitized, skill_results, context)
            elif blocked_actions:
                response = (
                    "I wanted to perform that action, but it was blocked by safety controls. "
                    + "; ".join(blocked_actions)
                )
            else:
                # Skill calls detected but none executed
                response = self.brain.strip_skill_blocks(response)
                if not response:
                    response = "I tried to process that, but couldn't complete the action."

        # Strip skill blocks and filter output
        response = finalize_response(response, self.brain, self.safety)

        # Add assistant response to memory
        self.memory.add_message("assistant", response)

        return response

    def _confirm_action(self, action_req, channel: Optional[BaseChannel] = None) -> bool:
        """Ask the user to confirm a high-risk action (CLI, Telegram, or Web)."""
        # 1. Try channel-specific confirmation (e.g. Telegram buttons)
        if channel:
            try:
                # Need to run async in a sync method
                # Since we are already in a thread (asyncio.to_thread), 
                # we can use the loop from the channel if it exists.
                if hasattr(channel, "application") and channel.application:
                    loop = channel.application.loop
                    future = asyncio.run_coroutine_threadsafe(
                        channel.request_user_confirmation(action_req), 
                        loop
                    )
                    return future.result()
            except Exception as e:
                logger.error(f"Channel confirmation failed: {e}")
                # Fallback to CLI if possible
        
        # 2. Fallback to CLI confirmation
        print_system(f"\n  Action requires confirmation:")
        if action_req.screenshot_path:
            print_system(f"    (Preview available: {action_req.screenshot_path})")
        print_system(f"    {action_req.summary()}")
        try:
            answer = input("  Approve? (y/n): ").strip().lower()
            if answer in ("y", "yes"):
                self.safety.approve_action(action_req.id)
                return True
            else:
                self.safety.deny_action(action_req.id)
                return False
        except (EOFError, KeyboardInterrupt):
            self.safety.deny_action(action_req.id)
            return False

    def _execute_skill(self, skill_name: str, parameters: dict) -> Optional[SkillResult]:
        """Execute a skill by name with given parameters."""
        if skill_name not in self._skills:
            logger.warning(f"Unknown skill: {skill_name}")
            return SkillResult(
                success=False,
                message=f"I don't have a skill called '{skill_name}'.",
            )

        skill = self._skills[skill_name]
        logger.info(f"Executing skill: {skill_name} with params: {parameters}")
        print_system(f"Executing: {skill_name}...")

        try:
            result = skill.execute(**parameters)
            print_skill_result(skill_name, result.message[:100], result.success)
            return result
        except Exception as e:
            logger.error(f"Skill execution error: {e}")
            return SkillResult(
                success=False,
                message=f"Error executing {skill_name}: {str(e)}",
            )

    def _handle_special_commands(self, user_input: str) -> Optional[str]:
        """Handle special meta-commands."""
        cmd = user_input.strip().lower()
        logger.debug(f"[SPECIAL COMMAND CHECK] Raw input: '{user_input}' -> Lower/stripped: '{cmd}'")

        if cmd in ("quit", "exit", "bye", "goodbye"):
            self._running = False
            msg = f"Goodbye, {self.config.user_name}. It was a pleasure."
            self.speaker.speak(msg)
            return msg

        # Intercept Telegram /daily commands parsed as structured sentences
        scheduler_skill = self._skills.get("scheduler")
        if scheduler_skill and scheduler_skill.scheduler:
            # Strip trailing punctuation and make lowercase for robust matching
            clean_cmd = cmd.lower().rstrip('.! ?')
            
            if clean_cmd == "turn the morning briefing on":
                scheduler_skill.scheduler.update_task("morning_briefing", enabled=True)
                return "Enabled the daily morning briefing. It will trigger at the configured time."
                
            if clean_cmd == "turn the morning briefing off":
                scheduler_skill.scheduler.update_task("morning_briefing", enabled=False)
                return "Disabled the daily morning briefing."
                
            if clean_cmd == "turn the observer mode on":
                if self.observer:
                    self.observer.start()
                    return "Enabled Observer Mode. I am now listening to the background."
                return "Observer mode requires a valid voice listener enabled."

            if clean_cmd == "turn the observer mode off":
                if self.observer:
                    self.observer.stop()
                    return "Disabled Observer Mode."
                return "Observer mode not configured."
                
            if clean_cmd.startswith("change the morning briefing schedule to "):
                time_str = clean_cmd.replace("change the morning briefing schedule to ", "").strip()
                try:
                    hour, minute = map(int, time_str.split(":"))
                    scheduler_skill.scheduler.update_task(
                        "morning_briefing", 
                        schedule={"hour": hour, "minute": minute}
                    )
                    return f"Morning briefing time configured for {hour:02d}:{minute:02d}."
                except ValueError:
                    return f"Invalid time format. Please use HH:MM (e.g., 08:30)."
                    
            if clean_cmd.startswith("update the good_morning routine to check the weather in "):
                city = clean_cmd.replace("update the good_morning routine to check the weather in ", "").strip()
                if scheduler_skill.routines:
                    steps = [
                        {"skill": "system_control", "parameters": {"action": "time"}, "label": "Current time"},
                        {"skill": "calendar", "parameters": {"action": "briefing"}, "label": "Today's schedule"},
                        {"skill": "web_search", "parameters": {"query": f"weather in {city} today", "max_results": 2}, "label": "Local weather"},
                        {"skill": "web_search", "parameters": {"query": "top news headlines today", "max_results": 3}, "label": "Today's news"},
                    ]
                    scheduler_skill.routines.update_routine("good_morning", steps)
                    return f"Location for the morning briefing updated to: {city.title()}."

        if cmd == "/models":
            models = self.brain.list_models()
            return f"Available Ollama models:\n" + "\n".join(f"  - {m}" for m in models)

        if cmd.startswith("/model "):
            model_name = cmd.split(" ", 1)[1]
            self.brain.change_model(model_name)
            return f"Switched to model: {model_name}"

        if cmd == "/memory":
            return self.memory.get_summary()

        if cmd == "/clear":
            self.memory.clear_session()
            return "Conversation history cleared."

        if cmd == "/export":
            path = self.memory.export_conversation()
            return f"Conversation exported to: {path}"

        if cmd == "/skills":
            skills_list = "\n".join(
                f"  - {name}: {skill.description}"
                for name, skill in self._skills.items()
            )
            return f"Available skills:\n{skills_list}"

        if cmd == "/schedules":
            if hasattr(self, '_scheduler_skill') and self._scheduler_skill.scheduler:
                tasks = self._scheduler_skill.scheduler.list_tasks()
                if not tasks:
                    return "No scheduled tasks configured."
                lines = [
                    f"  - [{('ON' if t['enabled'] else 'OFF')}] {t['name']} ({t['type']}, ID: {t['id']})"
                    for t in tasks
                ]
                return "Scheduled tasks:\n" + "\n".join(lines)
            return "Scheduler not available. Use --mode server to enable scheduling."

        if cmd == "/identity":
            files = self.identity.list_files()
            journals = self.identity.list_journals(5)
            return (
                f"Identity files: {', '.join(files)}\n"
                f"Recent journals: {', '.join(journals) if journals else 'none'}\n"
                f"Location: {self.identity.identity_dir}"
            )

        if cmd == "/help":
            return (
                "Available commands:\n"
                "  /models    - List available Ollama models\n"
                "  /model <name> - Switch LLM model\n"
                "  /memory    - Show memory status\n"
                "  /clear     - Clear conversation history\n"
                "  /export    - Export conversation to file\n"
                "  /skills    - List available skills\n"
                "  /schedules - List scheduled tasks\n"
                "  /identity  - Show identity system status\n"
                "  /help      - Show this help message\n"
                "  quit/exit/bye - Shut down W.I.N.S.T.O.N.\n"
            )

        return None

    def run_text_mode(self):
        """Run W.I.N.S.T.O.N. in text input mode."""
        print_banner()
        greeting = f"{get_greeting()}, {self.config.user_name}. I am {self.config.name}. How may I assist you?"
        print_winston(greeting)
        self.speaker.speak(greeting)

        self._running = True
        while self._running:
            try:
                user_input = input(f"\n  \033[32m\033[1mYou:\033[0m ").strip()
                if not user_input:
                    continue

                response = self.process_input(user_input)
                print_winston(response)

                # Speak the response
                self.speaker.speak(response)

            except EOFError:
                break
            except KeyboardInterrupt:
                break

        self._signal_handler(None, None)

    def run_voice_mode(self):
        """Run W.I.N.S.T.O.N. in voice input mode with wake word."""
        print_banner()
        greeting = f"{get_greeting()}, {self.config.user_name}. I am {self.config.name}. Voice mode active."
        print_winston(greeting)
        self.speaker.speak(greeting)

        if not self.listener:
            print_error("Voice mode requires listener! Check whisper installation.")
            return

        self._running = True

        if self.config.wake_word.enabled:
            print_system(f"Listening for wake word: '{self.config.wake_word.wake_word}'...")
            self.listener.listen_for_wake_word(callback=self._voice_callback)
        else:
            # Push-to-talk mode
            print_system("Push-to-talk mode: Press Enter to start listening")
            while self._running:
                try:
                    input("  [Press Enter to speak...]")
                    print_system("Listening...")
                    text = self.listener.listen()
                    if text:
                        self._voice_callback(text)
                except (EOFError, KeyboardInterrupt):
                    break

    def _voice_callback(self, text: str):
        """Handle transcribed voice input."""
        print_user(text)
        response = self.process_input(text)
        print_winston(response)
        self.speaker.speak(response)

    def _observer_callback(self, transcript: str):
        """Handle continuous background transcripts."""
        logger.info(f"[OBSERVER] Processing transcript chunk...")
        skill_calls = self.brain.analyze_observation(transcript)
        
        for call in skill_calls:
            skill_name = call.get("skill")
            params = call.get("parameters", {})
            if skill_name in self._skills:
                try:
                    logger.info(f"[OBSERVER] Autonomously executing: {skill_name}")
                    result = self._skills[skill_name].execute(**params)
                    if result and result.success:
                        logger.info(f"[OBSERVER] Success: {result.message}")
                        
                        # Notify the user via Telegram proactively
                        if getattr(self, "_telegram_channel", None) and self._telegram_channel._default_chat_id:
                            msg = (
                                "🔍 <b>Observer Action:</b>\n"
                                f"I noticed a detail in your background conversation and autonomously executed: <code>{skill_name}</code>.\n\n"
                                f"Result: {result.message}"
                            )
                            # Async schedule
                            asyncio.run_coroutine_threadsafe(
                                self._telegram_channel.send_message(
                                    self._telegram_channel._default_chat_id, 
                                    msg
                                ),
                                self._channel_loop
                            )
                except Exception as e:
                    logger.error(f"[OBSERVER] Action error executing {skill_name}: {e}")

    def run_hybrid_mode(self):
        """Run with both voice and text input available."""
        print_banner()
        greeting = (
            f"{get_greeting()}, {self.config.user_name}. I am {self.config.name}. "
            "Hybrid mode active - you can type or speak."
        )
        print_winston(greeting)
        self.speaker.speak(greeting)

        self._running = True

        # Start wake word listener in background thread
        if self.listener and self.config.wake_word.enabled:
            voice_thread = threading.Thread(
                target=self.listener.listen_for_wake_word,
                args=(self._voice_callback,),
                daemon=True,
            )
            voice_thread.start()
            print_system(f"Voice activated. Wake word: '{self.config.wake_word.wake_word}'")

        # Text input loop in main thread
        while self._running:
            try:
                user_input = input(f"\n  \033[32m\033[1mYou:\033[0m ").strip()
                if not user_input:
                    continue
                response = self.process_input(user_input)
                print_winston(response)
                self.speaker.speak(response)
            except (EOFError, KeyboardInterrupt):
                break

        self._signal_handler(None, None)

    def run(self):
        """Start W.I.N.S.T.O.N. in the configured input mode."""
        mode = self.config.input_mode
        logger.info(f"Starting W.I.N.S.T.O.N. in {mode} mode")

        if mode == "text":
            self.run_text_mode()
        elif mode == "voice":
            self.run_voice_mode()
        elif mode == "hybrid":
            self.run_hybrid_mode()
        else:
            print_error(f"Unknown input mode: {mode}. Using text mode.")
            self.run_text_mode()


def main():
    """Entry point for W.I.N.S.T.O.N."""
    import argparse

    parser = argparse.ArgumentParser(description="W.I.N.S.T.O.N. - AI Assistant")
    parser.add_argument(
        "--mode",
        choices=["text", "voice", "hybrid", "server"],
        default=None,
        help="Input mode: text, voice, hybrid, or server (web UI for phone access)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Ollama model to use (e.g., qwen2.5:7b, llama3.1:8b)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config YAML file",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for web server mode (default: 8000)",
    )

    args = parser.parse_args()

    # Load config
    config = load_config(args.config)

    # Apply CLI overrides
    if args.mode:
        config.input_mode = args.mode
    if args.model:
        config.ollama.model = args.model
    if args.debug:
        config.debug = True

    # Start W.I.N.S.T.O.N.
    try:
        if config.input_mode == "server":
            from winston.server import start_server
            start_server(config, port=args.port)
        else:
            winston = Winston(config)
            winston.run()
    except ConnectionError:
        print_error(
            "Could not connect to Ollama!\n"
            "  1. Install Ollama: https://ollama.ai\n"
            "  2. Start it: ollama serve\n"
            "  3. Pull a model: ollama pull qwen2.5:7b\n"
            "  4. Try again!"
        )
        sys.exit(1)
    except Exception as e:
        print_error(f"Fatal error: {e}")
        if config.debug:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
