"""
Brain module - LLM integration via Ollama.
Handles reasoning, tool/skill routing, and response generation.
Supports multiple models: Qwen, Llama, Mistral, DeepSeek, etc.
"""

import json
import logging
import re
from typing import Optional, Union, Generator

import httpx

from winston.config import OllamaConfig, WinstonConfig
from winston.utils.retry import retry_call, LLM_POLICY
from winston.core.providers import (
    LLMProvider, OllamaProvider, OpenAIProvider,
    AnthropicProvider, GeminiProvider, detect_provider, create_provider,
)
from winston.core.usage_tracker import UsageTracker
from winston.core.model_fallback import ProviderCooldown, run_with_fallback, classify_error, FailoverReason

logger = logging.getLogger("winston.brain")


    # Patterns that indicate sensitive data in conversation messages
_SENSITIVE_CONTENT_PATTERNS = [
    re.compile(r"(?i)password\s*[:=]\s*\S+"),
    re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)credit\s*card[\s:]*\d[\d\s-]{10,}"),
    re.compile(r"(?i)ssn[\s:]*\d{3}-?\d{2}-?\d{4}"),
]


class Brain:
    """
    The LLM-powered brain of W.I.N.S.T.O.N.
    Communicates with Ollama to process natural language and route to skills.
    """

    # Cloud providers that send data over the internet
    CLOUD_PROVIDERS = {"openai", "anthropic", "gemini"}

    def __init__(self, config: Union[OllamaConfig, WinstonConfig], identity=None):
        # Full WinstonConfig is preferred for multi-provider support
        if hasattr(config, "ollama"):
            # It's a WinstonConfig
            self.full_config = config
            self.config = config.ollama
        else:
            # Legacy/Single-config initialization (e.g. tests)
            self.full_config = None
            self.config = config

        self.client = httpx.Client(base_url=self.config.host, timeout=120.0)
        self.async_client = httpx.AsyncClient(base_url=self.config.host, timeout=120.0)
        self.available_skills: dict = {}
        self.identity = identity  # IdentityManager for dynamic system prompts

        # Usage tracking
        self.usage_tracker = UsageTracker()

        # Model fallback
        self._cooldowns = ProviderCooldown()
        self._fallback_config = getattr(self.full_config, "fallback", None) if self.full_config else None

        # Multi-provider support
        self._current_provider = "ollama"
        self._api_keys: dict[str, str] = {}
        self._providers: dict[str, LLMProvider] = {
            "ollama": OllamaProvider(self.config.host, model=self.config.model),
        }
        
        # Register cloud providers if full config is available
        if self.full_config:
            self._register_cloud_providers()

        self._verify_connection()

    def _register_cloud_providers(self):
        """Register cloud providers that have API keys configured."""
        if not self.full_config or not self.full_config.providers:
            return
            
        p = self.full_config.providers
        if p.openai_api_key:
            self._providers["openai"] = create_provider("openai", api_key=p.openai_api_key)
        if p.anthropic_api_key:
            self._providers["anthropic"] = create_provider("anthropic", api_key=p.anthropic_api_key)
        if p.gemini_api_key:
            self._providers["gemini"] = create_provider("gemini", api_key=p.gemini_api_key)
        if p.deepseek_api_key:
            self._providers["deepseek"] = create_provider("deepseek", api_key=p.deepseek_api_key)
        if p.openrouter_api_key:
            self._providers["openrouter"] = create_provider("openrouter", api_key=p.openrouter_api_key)
        if p.mistral_api_key:
            self._providers["mistral"] = create_provider("mistral", api_key=p.mistral_api_key)
        if p.xai_api_key:
            self._providers["xai"] = create_provider("xai", api_key=p.xai_api_key)
        if p.perplexity_api_key:
            self._providers["perplexity"] = create_provider("perplexity", api_key=p.perplexity_api_key)
        if p.huggingface_api_key:
            self._providers["huggingface"] = create_provider("huggingface", api_key=p.huggingface_api_key)
        if p.minimax_api_key:
            self._providers["minimax"] = create_provider("minimax", api_key=p.minimax_api_key)
        if p.glm_api_key:
            self._providers["glm"] = create_provider("glm", api_key=p.glm_api_key)
        if p.vercel_api_key:
            self._providers["vercel"] = create_provider("vercel", api_key=p.vercel_api_key)

    def _is_cloud_provider(self) -> bool:
        """Check if the current provider sends data over the internet."""
        return self._current_provider in self.CLOUD_PROVIDERS

    def _build_safe_system_prompt(self) -> str:
        """Build a system prompt that is safe to send to cloud providers.
        Strips personal identity data (USER.md, MEMORY.md, journals) and
        only includes the base personality + skill instructions.
        """
        base = self.config.system_prompt

        # For cloud: only include PERSONALITY.md (general AI behavior), NOT personal data
        if self.identity:
            personality = self.identity.read_file("PERSONALITY.md")
            if personality:
                base = personality + "\n\n---\n\n" + base

        base += (
            "\n\nPRIVACY NOTE: You are running on a CLOUD provider. "
            "The user's personal identity data, memory files, and journals "
            "have been excluded from this prompt to protect their privacy. "
            "Do NOT ask the user to share sensitive personal information in this mode."
        )
        return base

    def _sanitize_messages_for_cloud(self, messages: list[dict]) -> list[dict]:
        """Remove sensitive data from conversation messages before sending to cloud.
        Redacts passwords, API keys, private keys, and other secrets.
        """
        sanitized = []
        for msg in messages:
            content = msg.get("content", "")
            for pattern in _SENSITIVE_CONTENT_PATTERNS:
                content = pattern.sub("[REDACTED]", content)
            sanitized.append({**msg, "content": content})
        return sanitized

    def _verify_connection(self):
        """Verify Ollama is running and the model is available."""
        try:
            response = self.client.get("/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
            model_names = [m["name"] for m in models]
            logger.info(f"Connected to Ollama. Available models: {model_names}")

            # Check if our target model is available
            target = self.config.model
            if not any(target in name for name in model_names):
                logger.warning(
                    f"Model '{target}' not found. Available: {model_names}. "
                    f"Pull it with: ollama pull {target}"
                )
        except httpx.ConnectError:
            logger.error(
                "Cannot connect to Ollama! Make sure Ollama is running.\n"
                "Install: https://ollama.ai\n"
                "Start: ollama serve\n"
                f"Pull a model: ollama pull {self.config.model}"
            )
            raise ConnectionError("Ollama is not running")

    def register_skills(self, skills: dict):
        """Register available skills so the LLM knows what tools it can use."""
        self.available_skills = skills
        logger.info(f"Registered {len(skills)} skills: {list(skills.keys())}")

    def _build_tools_description(self) -> str:
        """Build a description of available tools/skills for the LLM."""
        if not self.available_skills:
            return ""

        tools_desc = "\n\nYou have ONLY these skills available (use the EXACT name shown):\n"
        skill_names = []
        for name, skill in self.available_skills.items():
            tools_desc += f"\n- **{name}**: {skill.description}"
            if skill.parameters:
                try:
                    tools_desc += f"\n  Parameters: {json.dumps(skill.parameters)}"
                except (TypeError, ValueError) as e:
                    logger.warning(f"Skill '{name}' parameters not serializable: {e}")
                    tools_desc += f"\n  Parameters: {skill.parameters!r}"
            skill_names.append(name)

        tools_desc += (
            f"\n\nAVAILABLE SKILL NAMES (use ONLY these exact names): {skill_names}\n"
            "You do NOT have a 'weather' skill. For weather, use 'web_search' with a weather query.\n\n"
            "IMPORTANT: When you need to use a skill, respond with ONLY the JSON block below and NOTHING else:\n"
            '```json\n{"skill": "skill_name", "parameters": {"param1": "value1"}}\n```\n'
            "Do NOT add any text before or after the JSON block. No explanations, no greetings, no follow-ups.\n"
            "If no skill is needed, respond with normal text only (no JSON blocks at all).\n"
            "NEVER mix text and JSON in the same response."
        )
        return tools_desc

    def _get_image_description(self, images: list[str], user_query: str = "") -> str:
        """Use the vision model to get a detailed description of the images.

        Passes the user's actual question to the vision model for focused analysis.
        """
        if not images:
            return ""

        logger.info(f"Analyzing images using vision model: {self.config.vision_model}")

        # Pass the user's question so the vision model knows what to focus on
        user_prompt = user_query if user_query else "Describe what you see in this image in detail."

        vision_messages = [
            {"role": "system", "content": (
                "You are a highly accurate vision assistant. Analyze the image carefully and precisely. "
                "Describe exactly what you see — UI elements, text, labels, buttons, icons, layout. "
                "Do NOT guess or make up details that are not visible. "
                "If you see text in a specific language, mention that language and translate the text."
            )},
            {"role": "user", "content": user_prompt, "images": images}
        ]

        try:
            payload = {
                "model": self.config.vision_model,
                "messages": vision_messages,
                "stream": False,
                "options": {"temperature": 0.1, "num_ctx": 4096},
            }
            response = self.client.post("/api/chat", json=payload)
            response.raise_for_status()
            description = response.json()["message"]["content"]
            logger.info(f"Vision analysis complete: {description[:100]}...")
            return description
        except Exception as e:
            logger.error(f"Vision analysis failed: {e}")
            return "[Error analyzing image]"

    async def _get_image_description_async(self, images: list[str], user_query: str = "") -> str:
        """Async version of image description.

        Passes the user's actual question to the vision model for focused analysis.
        """
        if not images:
            return ""

        logger.info(f"Analyzing images (async) using vision model: {self.config.vision_model}")

        user_prompt = user_query if user_query else "Describe what you see in this image in detail."

        vision_messages = [
            {"role": "system", "content": (
                "You are a highly accurate vision assistant. Analyze the image carefully and precisely. "
                "Describe exactly what you see — UI elements, text, labels, buttons, icons, layout. "
                "Do NOT guess or make up details that are not visible. "
                "If you see text in a specific language, mention that language and translate the text."
            )},
            {"role": "user", "content": user_prompt, "images": images}
        ]

        try:
            payload = {
                "model": self.config.vision_model,
                "messages": vision_messages,
                "stream": False,
                "options": {"temperature": 0.1, "num_ctx": 4096},
            }
            response = await self.async_client.post("/api/chat", json=payload)
            response.raise_for_status()
            result = response.json()
            description = result["message"]["content"]
            logger.info(f"Vision analysis complete (async): {description[:100]}...")
            return description
        except Exception as e:
            logger.error(f"Async vision analysis failed: {e}")
            return "[Error analyzing image]"

    def think(
        self,
        user_input: str,
        conversation_history: list[dict] = None,
        system_override: str = None,
        images: list[str] = None,
    ) -> str:
        """
        Process user input through the LLM and return a response.

        Args:
            user_input: The user's message/command
            conversation_history: Previous messages for context
            system_override: Override the default system prompt
            images: Optional list of base64-encoded images

        Returns:
            The LLM's response text
        """
        if images and self._current_provider == "ollama":
            vision_mode = getattr(self.config, "vision_mode", "two-step")

            if vision_mode == "direct":
                # DIRECT MODE: Send ONLY the image + question to the vision model.
                # No conversation history (it confuses vision models and slows them down).
                # No tools description (vision model doesn't execute skills).
                logger.info(f"Direct vision mode: using {self.config.vision_model}")

                vision_system = (
                    "You are a vision assistant. Analyze the image the user sends and "
                    "respond directly to their question. Be accurate, concise, and describe "
                    "only what you actually see in THIS image. Do not guess or hallucinate."
                )

                user_content = user_input or "What do you see in this image?"
                messages = [
                    {"role": "system", "content": vision_system},
                    {"role": "user", "content": user_content, "images": images},
                ]

                try:
                    payload = {
                        "model": self.config.vision_model,
                        "messages": messages,
                        "stream": False,
                        "options": {
                            "temperature": 0.1,  # Low temp for accurate image analysis
                        },
                    }
                    response = self.client.post("/api/chat", json=payload)
                    response.raise_for_status()
                    result = response.json()
                    assistant_message = result["message"]["content"]
                    logger.debug(f"Direct vision response: {assistant_message[:200]}...")
                    return assistant_message
                except Exception as e:
                    logger.warning(f"Direct vision failed ({e}), falling back to two-step mode")
                    # Fall through to two-step mode below
                    vision_mode = "two-step"

            if vision_mode == "two-step":
                # TWO-STEP MODE: Vision model describes, main model responds
                image_description = self._get_image_description(images, user_query=user_input)
                user_input = (
                    f"BACKGROUND CONTEXT: The user has provided an image. "
                    f"I have analyzed it and here is the visual description: {image_description}.\n\n"
                    f"USER REQUEST: {user_input or 'Please describe what you see.'}"
                )
                images = None

        # ── Build system prompt (privacy-aware) ──────────
        if system_override:
            # Caller provided an explicit prompt (e.g. scheduler summary).
            # Skip tool descriptions — not needed for pure text tasks.
            system_prompt = system_override
        elif self._is_cloud_provider():
            # Cloud provider: strip personal identity data
            system_prompt = self._build_safe_system_prompt()
            system_prompt += self._build_tools_description()
        elif self.identity:
            system_prompt = self.identity.build_system_prompt(self.config.system_prompt)
            system_prompt += self._build_tools_description()
        else:
            system_prompt = self.config.system_prompt
            system_prompt += self._build_tools_description()

        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history
        if conversation_history:
            messages.extend(conversation_history)

        # Add current user input (now containing the image description if applicable)
        messages.append({"role": "user", "content": user_input})

        # ── Sanitize messages for cloud providers ──────────
        if self._is_cloud_provider():
            messages = self._sanitize_messages_for_cloud(messages)

        try:
            # Use provider system for non-Ollama providers
            if self._current_provider != "ollama":
                provider = self._providers.get(self._current_provider)
                if provider:
                    # Update provider's model to match current config
                    if hasattr(provider, "model"):
                        provider.model = self.config.model

                    def _provider_call():
                        return provider.chat(
                            messages=messages,
                            temperature=self.config.temperature,
                            images=images,
                        )

                    assistant_message = retry_call(_provider_call, policy=LLM_POLICY)
                    self.usage_tracker.record(self._current_provider, self.config.model, provider.last_usage)
                    logger.debug(f"LLM response ({self._current_provider}): {assistant_message[:200]}...")
                    return assistant_message

            # Default: Ollama direct API
            payload = {
                "model": self.config.model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": self.config.temperature,
                    "num_ctx": self.config.context_window,
                },
            }

            def _ollama_call():
                resp = self.client.post("/api/chat", json=payload)
                resp.raise_for_status()
                return resp.json()

            result = retry_call(_ollama_call, policy=LLM_POLICY)
            assistant_message = result["message"]["content"]
            # Record Ollama usage
            self.usage_tracker.record("ollama", self.config.model, {
                "input": result.get("prompt_eval_count", 0),
                "output": result.get("eval_count", 0),
            })
            logger.debug(f"LLM response: {assistant_message[:200]}...")
            return assistant_message

        except Exception as e:
            # Try fallback chain if enabled
            if (self._fallback_config and self._fallback_config.enabled
                    and self._fallback_config.chain):
                reason = classify_error(e)
                if reason != FailoverReason.CONTEXT_OVERFLOW:
                    logger.warning(f"Primary provider failed ({reason.value}), trying fallback chain")
                    try:
                        fallback_chain = [
                            (entry.get("provider", ""), entry.get("model", ""))
                            for entry in self._fallback_config.chain
                        ]

                        def _fallback_run(prov_name, model_name):
                            prov = self._providers.get(prov_name)
                            if not prov:
                                prov = create_provider(
                                    prov_name, model=model_name,
                                    api_key=self._api_keys.get(prov_name, ""),
                                )
                            prov.model = model_name
                            result = prov.chat(messages=messages, temperature=self.config.temperature, images=images)
                            self.usage_tracker.record(prov_name, model_name, prov.last_usage)
                            return result

                        response, used_prov, used_model = run_with_fallback(
                            primary=(self._current_provider, self.config.model),
                            fallbacks=fallback_chain,
                            run_fn=_fallback_run,
                            cooldowns=self._cooldowns,
                        )
                        logger.info(f"Fallback succeeded: {used_prov}/{used_model}")
                        return response
                    except Exception as fallback_err:
                        logger.error(f"All fallbacks failed: {fallback_err}")

            if isinstance(e, httpx.HTTPStatusError):
                logger.error(f"Ollama API error: {e}")
                return "I'm having trouble processing that request. Please try again."
            logger.error(f"Brain error: {e}")
            return "I encountered an unexpected error. Please check the logs."

    async def think_async(
        self,
        user_input: str,
        conversation_history: list[dict] = None,
        system_override: str = None,
        images: list[str] = None,
    ) -> str:
        """Async version of think() for non-blocking operation."""
        if images and self._current_provider == "ollama":
            vision_mode = getattr(self.config, "vision_mode", "two-step")

            if vision_mode == "direct":
                # DIRECT MODE: Send ONLY the image + question to the vision model.
                # No conversation history (confuses vision models and slows them down).
                logger.info(f"Direct vision mode (async): using {self.config.vision_model}")

                vision_system = (
                    "You are a vision assistant. Analyze the image the user sends and "
                    "respond directly to their question. Be accurate, concise, and describe "
                    "only what you actually see in THIS image. Do not guess or hallucinate."
                )

                user_content = user_input or "What do you see in this image?"
                messages = [
                    {"role": "system", "content": vision_system},
                    {"role": "user", "content": user_content, "images": images},
                ]

                try:
                    payload = {
                        "model": self.config.vision_model,
                        "messages": messages,
                        "stream": False,
                        "options": {
                            "temperature": 0.1,  # Low temp for accurate image analysis
                        },
                    }
                    response = await self.async_client.post("/api/chat", json=payload)
                    response.raise_for_status()
                    result = response.json()
                    return result["message"]["content"]
                except Exception as e:
                    logger.warning(f"Direct vision failed (async) ({e}), falling back to two-step mode")
                    vision_mode = "two-step"

            if vision_mode == "two-step":
                # TWO-STEP MODE: Vision model describes, main model responds
                image_description = await self._get_image_description_async(images, user_query=user_input)
                user_input = (
                    f"BACKGROUND CONTEXT: The user has provided an image. "
                    f"I have analyzed it and here is the visual description: {image_description}.\n\n"
                    f"USER REQUEST: {user_input or 'Please describe what you see.'}"
                )
                images = None

        # ── Build system prompt (privacy-aware) ──────────
        if system_override:
            system_prompt = system_override
        elif self._is_cloud_provider():
            system_prompt = self._build_safe_system_prompt()
            system_prompt += self._build_tools_description()
        elif self.identity:
            system_prompt = self.identity.build_system_prompt(self.config.system_prompt)
            system_prompt += self._build_tools_description()
        else:
            system_prompt = self.config.system_prompt
            system_prompt += self._build_tools_description()

        messages = [{"role": "system", "content": system_prompt}]
        if conversation_history:
            messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_input})

        # ── Sanitize messages for cloud providers ──────────
        if self._is_cloud_provider():
            messages = self._sanitize_messages_for_cloud(messages)

        try:
            # Use provider system for non-Ollama providers
            if self._current_provider != "ollama":
                provider = self._providers.get(self._current_provider)
                if provider:
                    if hasattr(provider, "model"):
                        provider.model = self.config.model
                        
                    assistant_message = await provider.chat_async(
                        messages=messages,
                        temperature=self.config.temperature,
                        images=images,
                    )
                    logger.debug(f"Async LLM response ({self._current_provider}): {assistant_message[:200]}...")
                    return assistant_message

            # Default: Ollama direct API
            payload = {
                "model": self.config.model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": self.config.temperature,
                    "num_ctx": self.config.context_window,
                },
            }

            response = await self.async_client.post("/api/chat", json=payload)

            response.raise_for_status()
            result = response.json()
            return result["message"]["content"]
        except Exception as e:
            logger.error(f"Async brain error: {e}")
            return "I encountered an error processing your request."

    def analyze_observation(self, transcript: str) -> list[dict]:
        """
        Analyze a continuous conversation transcript and extract actionable items
        like calendar events or notes, returning them as parsed skill calls.
        """
        prompt = (
            "You are W.I.N.S.T.O.N., analyzing a transcript from a background meeting or conversation.\n"
            "If you identify any clear ACTION ITEMS, APPOINTMENTS, or IMPORTANT NOTES "
            "that I should remember or schedule, output the appropriate skill calls.\n"
            "If the conversation is just chatter with no concrete actionable info, DO NOT output any skills.\n"
            "You must output JSON skill calls inside `<skill>` tags.\n\n"
        )
        prompt += self._build_tools_description()
        
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Analyze this transcript chunk:\n\n{transcript}"}
        ]

        # Sanitize for cloud providers
        if self._is_cloud_provider():
            messages = self._sanitize_messages_for_cloud(messages)

        try:
            if self._current_provider != "ollama":
                provider = self._providers.get(self._current_provider)
                if provider:
                    assistant_message = provider.chat(
                        model=self.config.model,
                        messages=messages,
                        temperature=0.3, # Low temperature for analytical tasks
                    )
                    return self.extract_skill_calls(assistant_message)

            # Ollama
            response = self.client.post(
                "/api/chat",
                json={
                    "model": self.config.model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": 0.3},
                },
            )
            response.raise_for_status()
            result = response.json()
            return self.extract_skill_calls(result["message"]["content"])
        except Exception as e:
            logger.error(f"Brain observation analysis error: {e}")
            return []


    def think_stream(
        self,
        user_input: str,
        conversation_history: list[dict] = None,
    ):
        """
        Stream the LLM response token by token for real-time output.
        Yields response chunks as they arrive.
        """
        if self.identity:
            system_prompt = self.identity.build_system_prompt(self.config.system_prompt)
        else:
            system_prompt = self.config.system_prompt
        system_prompt += self._build_tools_description()
        messages = [{"role": "system", "content": system_prompt}]
        if conversation_history:
            messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_input})

        try:
            with self.client.stream(
                "POST",
                "/api/chat",
                json={
                    "model": self.config.model,
                    "messages": messages,
                    "stream": True,
                    "options": {
                        "temperature": self.config.temperature,
                        "num_ctx": self.config.context_window,
                    },
                },
            ) as response:
                for line in response.iter_lines():
                    if line:
                        data = json.loads(line)
                        if "message" in data and "content" in data["message"]:
                            yield data["message"]["content"]
                        if data.get("done", False):
                            break
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield "I encountered an error while streaming the response."

    # Map of common LLM hallucinated skill names to actual skill names
    SKILL_ALIASES = {
        "weather": "web_search",
        "search": "web_search",
        "google": "web_search",
        # Web Fetch (lightweight page reading)
        "fetch": "web_fetch",
        "get_page": "web_fetch",
        "read_page": "web_fetch",
        "read_url": "web_fetch",
        "webpage": "web_fetch",
        "get_url": "web_fetch",
        "download_page": "web_fetch",
        "extract": "web_fetch",
        "page_content": "web_fetch",
        "scrape": "web_fetch",
        "fetch_url": "web_fetch",
        "read_website": "web_fetch",
        # Browser (interactive pages)
        "web": "browser",
        "chrome": "browser",
        "safari": "browser",
        "navigate": "browser",
        "web_screenshot": "browser",
        "screenshot_page": "browser",
        "screenshot": "browser",
        "reminder": "calendar",
        "reminders": "calendar",
        "system": "system_control",
        "terminal": "system_control",
        "shell": "system_control",
        "command": "system_control",
        "open": "system_control",
        # Screenshot & Vision
        "screen": "screenshot",
        "screencapture": "screenshot",
        "screen_capture": "screenshot",
        "take_screenshot": "screenshot",
        "vision": "screenshot",
        "ocr": "screenshot",
        # YouTube
        "yt": "youtube",
        "video": "youtube",
        "play_video": "youtube",
        "youtube_search": "youtube",
        # File Manager
        "files": "file_manager",
        "file": "file_manager",
        "filesystem": "file_manager",
        "find_file": "file_manager",
        "read_file": "file_manager",
        "create_file": "file_manager",
        "directory": "file_manager",
        # Clipboard
        "copy": "clipboard",
        "paste": "clipboard",
        "clip": "clipboard",
        "clipboard_manager": "clipboard",
        # Calendar & Reminders
        "schedule": "calendar",
        "event": "calendar",
        "events": "calendar",
        "remind_me": "calendar",
        "alarm": "calendar",
        "timer": "calendar",
        "appointment": "calendar",
        # Smart Home / URL
        "url": "smart_home",
        "website": "smart_home",
        "open_url": "smart_home",
        "bookmark": "smart_home",
        "bookmarks": "smart_home",
        "device": "smart_home",
        "light": "smart_home",
        "lights": "smart_home",
        "iot": "smart_home",
        # Code Runner
        "code": "code_runner",
        "python": "code_runner",
        "execute": "code_runner",
        "run_code": "code_runner",
        "calculate": "code_runner",
        "calc": "code_runner",
        "compute": "code_runner",
        "eval": "code_runner",
        "script": "code_runner",
        "math": "code_runner",
        # Scheduler
        "cron": "scheduler",
        "schedule_task": "scheduler",
        "automation": "scheduler",
        "scheduled": "scheduler",
        "scheduled_tasks": "scheduler",
        # Audio Analysis
        "audio": "audio_analysis",
        "transcribe": "audio_analysis",
        "summarize_audio": "audio_analysis",
        "podcast": "audio_analysis",
        "meeting_notes": "audio_analysis",
        "voice_note": "audio_analysis",
        # Travel / Flights
        "flights": "travel",
        "flight": "travel",
        "hotel": "travel",
        "hotels": "travel",
        "travel_search": "travel",
        "flight_search": "travel",
        "amadeus": "travel",
        "booking": "travel",
        "google_flights": "travel",
        "expedia": "travel",
        "skyscanner": "travel",
        "kayak": "travel",
        "flights_search": "travel",
        # Google Calendar
        "gcal": "google_calendar",
        "google_cal": "google_calendar",
        "book_appointment": "google_calendar",
        "google_events": "google_calendar",
        "google_schedule": "google_calendar",
        # Price Monitor
        "watch": "price_monitor",
        "price_watch": "price_monitor",
        "track_price": "price_monitor",
        "monitor_price": "price_monitor",
        "price_drop": "price_monitor",
        "deal_alert": "price_monitor",
        "price_alert": "price_monitor",
        "watch_price": "price_monitor",
        "price_tracker": "price_monitor",
        "price_check": "price_monitor",
    }

    def _resolve_skill_call(self, parsed: dict) -> dict:
        """Resolve hallucinated skill names and fix parameter mismatches."""
        skill_name = parsed.get("skill", "")
        params = parsed.get("parameters", {})

        # Map alias to real skill name
        if skill_name not in self.available_skills and skill_name in self.SKILL_ALIASES:
            real_name = self.SKILL_ALIASES[skill_name]
            logger.info(f"Resolved skill alias '{skill_name}' -> '{real_name}'")
            skill_name = real_name
            parsed["skill"] = real_name

        # Fix parameter mismatches for web_search
        if skill_name == "web_search" and "query" not in params:
            # The LLM might have used 'location', 'search', 'term', etc.
            for key in ["location", "search", "term", "keyword", "q"]:
                if key in params:
                    params["query"] = params.pop(key)
                    break
            else:
                # Build query from all parameter values
                params["query"] = " ".join(str(v) for v in params.values())
            if "max_results" not in params:
                params["max_results"] = 5
            parsed["parameters"] = params

        # Fix parameter mismatches for web_fetch
        if skill_name == "web_fetch" and "url" not in params:
            for key in ["link", "page", "address", "webpage", "site", "href"]:
                if key in params:
                    params["url"] = params.pop(key)
                    break
            parsed["parameters"] = params

        return parsed

    def parse_skill_calls(self, response: str) -> list[dict]:
        """
        Parse the LLM response for skill/tool invocations.
        Handles multiple formats the LLM might emit:
          1. {"skill": "name", "parameters": {}}  (preferred)
          2. {"skillname": {"action": ...}}          (nested shorthand)
          3. {"skillname": "action", "title": ...}   (flat shorthand)
        Returns a list of skill calls found in the response.
        """
        skill_calls = []
        import re

        # Fast path: if the entire response is a single JSON object, parse directly.
        # This handles deeply nested JSON (e.g. run_script with JS code) that
        # regex-based extraction misses.
        stripped = response.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = json.loads(stripped)
                if "skill" in parsed:
                    return [self._resolve_skill_call(parsed)]
                for key in list(parsed.keys()):
                    if key in self.available_skills:
                        value = parsed[key]
                        if isinstance(value, dict):
                            params = value
                        elif isinstance(value, str):
                            params = {k: v for k, v in parsed.items() if k != key}
                            params["action"] = value
                        else:
                            continue
                        return [self._resolve_skill_call({"skill": key, "parameters": params})]
            except json.JSONDecodeError:
                pass

        # Collect all JSON-looking blocks (both fenced and bare)
        raw_blocks = []
        for m in re.findall(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL):
            raw_blocks.append(m)
        if not raw_blocks:
            for m in re.findall(r'(\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\})', response, re.DOTALL):
                raw_blocks.append(m)

        for block in raw_blocks:
            try:
                parsed = json.loads(block)
            except json.JSONDecodeError:
                continue

            # Format 1: {"skill": "name", "parameters": {...}}
            if "skill" in parsed:
                skill_calls.append(self._resolve_skill_call(parsed))
                continue

            # Formats 2 & 3: skill name appears as a top-level key
            for key in list(parsed.keys()):
                if key in self.available_skills:
                    value = parsed[key]
                    if isinstance(value, dict):
                        # Format 2: {"calendar": {"action": "create_event", ...}}
                        params = value
                    elif isinstance(value, str):
                        # Format 3: {"calendar": "create_event", "title": "...", ...}
                        params = {k: v for k, v in parsed.items() if k != key}
                        params["action"] = value
                    else:
                        continue
                    resolved = self._resolve_skill_call({"skill": key, "parameters": params})
                    skill_calls.append(resolved)
                    break

        return skill_calls

    @staticmethod
    def strip_skill_blocks(response: str) -> str:
        """
        Remove all JSON skill blocks from a response, leaving only conversational text.
        Used to clean responses before displaying to user.
        """
        import re
        # Remove ```json {...} ``` blocks
        cleaned = re.sub(r"```json\s*\{.*?\}\s*```", "", response, flags=re.DOTALL)
        # Remove bare {"skill": ...} blocks
        cleaned = re.sub(r'\{\s*"skill"\s*:.*?\}', "", cleaned, flags=re.DOTALL)
        # Remove shorthand {"skillname": {...}} blocks
        cleaned = re.sub(r'\{\s*"[a-z_]+"\s*:\s*\{.*?\}\s*\}', "", cleaned, flags=re.DOTALL)
        # Clean up excess whitespace
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned

    def change_model(self, model_name: str):
        """Switch to a different model. Auto-detects provider from model name."""
        provider = detect_provider(model_name)
        if provider and provider != "ollama":
            if provider in self._providers:
                self._current_provider = provider
                self.config.model = model_name
                logger.info(f"Switched to {provider}:{model_name}")
            else:
                logger.warning(f"Provider '{provider}' not configured. Set API key first.")
        else:
            self._current_provider = "ollama"
            self.config.model = model_name
            logger.info(f"Switched to ollama:{model_name}")

    def list_models(self) -> list[dict]:
        """List all available models from all configured providers."""
        all_models = []
        for name, provider in self._providers.items():
            try:
                models = provider.list_models()
                for m in models:
                    all_models.append({"name": m, "provider": name})
            except Exception as e:
                logger.error(f"Error listing {name} models: {e}")
        return all_models

    def set_api_key(self, provider_name: str, api_key: str):
        """Set API key for a cloud provider, creating the provider instance."""
        self._api_keys[provider_name] = api_key
        provider_map = {
            "openai": OpenAIProvider,
            "anthropic": AnthropicProvider,
            "gemini": GeminiProvider,
        }
        cls = provider_map.get(provider_name)
        if cls:
            self._providers[provider_name] = cls(api_key)
            logger.info(f"API key set for {provider_name}")

    def get_api_keys(self) -> dict[str, bool]:
        """Return which providers have API keys configured."""
        return {name: True for name in self._api_keys}

    def get_current_provider(self) -> str:
        """Get the name of the current active provider."""
        return self._current_provider

    def switch_provider(self, provider_name: str) -> bool:
        """Switch to a different provider."""
        if provider_name in self._providers:
            self._current_provider = provider_name
            logger.info(f"Switched provider to {provider_name}")
            return True
        return False

    def close(self):
        """Clean up resources."""
        self.client.close()
