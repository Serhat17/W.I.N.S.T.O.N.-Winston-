"""
LLM Provider abstraction layer.
Supports Ollama (local), OpenAI, Anthropic, and Google Gemini APIs.
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Generator, Optional

import httpx

logger = logging.getLogger("winston.providers")


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    name: str = "base"
    last_usage: Optional[dict] = None  # {input: int, output: int, total: int}

    @abstractmethod
    def chat(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096, images: list[str] = None) -> str:
        """Send messages and get a complete response."""

    @abstractmethod
    async def chat_async(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096, images: list[str] = None) -> str:
        """Async version of chat."""

    @abstractmethod
    def chat_stream(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096) -> Generator[str, None, None]:
        """Stream response tokens."""

    @abstractmethod
    async def chat_stream_async(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096):
        """Async stream response tokens."""

    def list_models(self) -> list[str]:
        """List available models for this provider."""
        return []

    def verify(self) -> bool:
        """Verify the provider is operational."""
        return True


class OllamaProvider(LLMProvider):
    """Local Ollama provider."""

    name = "ollama"

    def __init__(self, host: str = "http://localhost:11434", model: str = "qwen2.5:7b", context_window: int = 8192):
        self.host = host
        self.model = model
        self.context_window = context_window
        self.client = httpx.Client(base_url=host, timeout=120.0)
        self.async_client = httpx.AsyncClient(base_url=host, timeout=120.0)

    def chat(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096, images: list[str] = None) -> str:
        # For /api/chat, images should be attached to the message they belong to (usually the last user message)
        if images and messages:
            # Create a shallow copy of messages to avoid modifying the original list in-place
            messages = list(messages)
            if messages[-1]["role"] == "user":
                # Create a copy of the last message to add images
                last_msg = messages[-1].copy()
                last_msg["images"] = images
                messages[-1] = last_msg

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": self.context_window},
        }

        response = self.client.post("/api/chat", json=payload)
        response.raise_for_status()
        result = response.json()
        self.last_usage = {
            "input": result.get("prompt_eval_count", 0),
            "output": result.get("eval_count", 0),
            "total": result.get("prompt_eval_count", 0) + result.get("eval_count", 0),
        }
        return result["message"]["content"]

    async def chat_async(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096, images: list[str] = None) -> str:
        if images and messages:
            messages = list(messages)
            if messages[-1]["role"] == "user":
                last_msg = messages[-1].copy()
                last_msg["images"] = images
                messages[-1] = last_msg

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": self.context_window},
        }

        response = await self.async_client.post("/api/chat", json=payload)
        response.raise_for_status()
        result = response.json()
        self.last_usage = {
            "input": result.get("prompt_eval_count", 0),
            "output": result.get("eval_count", 0),
            "total": result.get("prompt_eval_count", 0) + result.get("eval_count", 0),
        }
        return result["message"]["content"]

    def chat_stream(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096) -> Generator[str, None, None]:
        with self.client.stream(
            "POST",
            "/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": True,
                "options": {"temperature": temperature, "num_ctx": self.context_window},
            },
        ) as response:
            for line in response.iter_lines():
                if line:
                    data = json.loads(line)
                    if "message" in data and "content" in data["message"]:
                        yield data["message"]["content"]
                    if data.get("done", False):
                        break

    async def chat_stream_async(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096):
        async with self.async_client.stream(
            "POST",
            "/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": True,
                "options": {"temperature": temperature, "num_ctx": self.context_window},
            },
        ) as response:
            async for line in response.aiter_lines():
                if line:
                    data = json.loads(line)
                    if "message" in data and "content" in data["message"]:
                        yield data["message"]["content"]
                    if data.get("done", False):
                        break

    def list_models(self) -> list[str]:
        try:
            response = self.client.get("/api/tags")
            response.raise_for_status()
            return [m["name"] for m in response.json().get("models", [])]
        except Exception:
            return []

    def verify(self) -> bool:
        try:
            response = self.client.get("/api/tags")
            response.raise_for_status()
            models = [m["name"] for m in response.json().get("models", [])]
            if not any(self.model in m for m in models):
                logger.warning(f"Model '{self.model}' not found in Ollama. Available: {models}")
            return True
        except Exception as e:
            logger.error(f"Cannot connect to Ollama: {e}")
            return False

    def close(self):
        self.client.close()


class OpenAIProvider(LLMProvider):
    """OpenAI API provider (GPT-4, GPT-3.5, etc.)."""

    name = "openai"

    MODELS = [
        "gpt-5.4-pro", "gpt-5.4", "gpt-5.2", "gpt-5.2-pro",
        "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo",
        "o1", "o1-mini", "o1-preview", "o3-mini",
    ]

    # Models that require max_completion_tokens instead of max_tokens
    _NEW_TOKEN_PARAM_MODELS = {"gpt-5", "gpt-5.", "o1", "o3", "o4"}

    def _use_new_tokens_param(self) -> bool:
        return any(self.model.startswith(p) for p in self._NEW_TOKEN_PARAM_MODELS)

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.client = httpx.Client(timeout=120.0)
        self.async_client = httpx.AsyncClient(timeout=120.0)

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096, images: list[str] = None) -> str:
        # OpenAI supports images in the content list
        processed_messages = []
        for msg in messages:
            if msg["role"] == "user" and images and msg == messages[-1]:
                content = [{"type": "text", "text": msg["content"]}]
                for img in images:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img}"}
                    })
                processed_messages.append({"role": "user", "content": content})
            else:
                processed_messages.append(msg)

        payload = {
            "model": self.model,
            "messages": processed_messages,
            "temperature": temperature,
        }
        if self._use_new_tokens_param():
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens

        response = self.client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage", {})
        self.last_usage = {
            "input": usage.get("prompt_tokens", 0),
            "output": usage.get("completion_tokens", 0),
            "total": usage.get("total_tokens", 0),
        }
        return data["choices"][0]["message"]["content"]

    async def chat_async(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096, images: list[str] = None) -> str:
        processed_messages = []
        for msg in messages:
            if msg["role"] == "user" and images and msg == messages[-1]:
                content = [{"type": "text", "text": msg["content"]}]
                for img in images:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img}"}
                    })
                processed_messages.append({"role": "user", "content": content})
            else:
                processed_messages.append(msg)

        payload = {
            "model": self.model,
            "messages": processed_messages,
            "temperature": temperature,
        }
        if self._use_new_tokens_param():
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens

        response = await self.async_client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage", {})
        self.last_usage = {
            "input": usage.get("prompt_tokens", 0),
            "output": usage.get("completion_tokens", 0),
            "total": usage.get("total_tokens", 0),
        }
        return data["choices"][0]["message"]["content"]

    def chat_stream(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096) -> Generator[str, None, None]:
        token_key = ("max_completion_tokens" if self._use_new_tokens_param()
                     else "max_tokens")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            token_key: max_tokens,
            "stream": True,
        }
        with self.client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
        ) as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                        delta = data["choices"][0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            yield delta["content"]
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    async def chat_stream_async(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096):
        token_key = ("max_completion_tokens" if self._use_new_tokens_param()
                     else "max_tokens")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            token_key: max_tokens,
            "stream": True,
        }
        async with self.async_client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                        delta = data["choices"][0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            yield delta["content"]
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    def list_models(self) -> list[str]:
        return self.MODELS

    def verify(self) -> bool:
        if not self.api_key:
            return False
        try:
            response = self.client.get(
                f"{self.base_url}/models",
                headers=self._headers(),
            )
            return response.status_code == 200
        except Exception:
            return False

    def close(self):
        self.client.close()


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek API provider."""
    name = "deepseek"
    def __init__(self, api_key: str, model: str = "deepseek-chat"):
        super().__init__(api_key, model, base_url="https://api.deepseek.com")

class OpenRouterProvider(OpenAIProvider):
    """OpenRouter API provider."""
    name = "openrouter"
    def __init__(self, api_key: str, model: str = "google/gemini-2.0-flash-001"):
        super().__init__(api_key, model, base_url="https://openrouter.ai/api/v1")
    def _headers(self):
        h = super()._headers()
        h["HTTP-Referer"] = "https://github.com/winston-ai"
        h["X-Title"] = "W.I.N.S.T.O.N."
        return h

class XAIProvider(OpenAIProvider):
    """xAI (Grok) API provider."""
    name = "xai"
    def __init__(self, api_key: str, model: str = "grok-beta"):
        super().__init__(api_key, model, base_url="https://api.x.ai/v1")

class PerplexityProvider(OpenAIProvider):
    """Perplexity API provider."""
    name = "perplexity"
    def __init__(self, api_key: str, model: str = "llama-3.1-sonar-large-128k-online"):
        super().__init__(api_key, model, base_url="https://api.perplexity.ai")

class MistralProvider(OpenAIProvider):
    """Mistral AI API provider."""
    name = "mistral"
    def __init__(self, api_key: str, model: str = "mistral-large-latest"):
        super().__init__(api_key, model, base_url="https://api.mistral.ai/v1")


class AnthropicProvider(LLMProvider):
    """Anthropic API provider (Claude models)."""

    name = "anthropic"

    MODELS = [
        "claude-sonnet-4-20250514", "claude-haiku-4-20250514",
        "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
    ]

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.anthropic.com/v1"
        self.client = httpx.Client(timeout=120.0)
        self.async_client = httpx.AsyncClient(timeout=120.0)

    def _headers(self):
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def _convert_messages(self, messages: list[dict], images: list[str] = None) -> tuple[str, list[dict]]:
        """Anthropic requires system prompt separate from messages and specific image format."""
        system = ""
        converted = []
        for msg in messages:
            if msg["role"] == "system":
                system += msg["content"] + "\n"
            elif msg["role"] == "user" and images and msg == messages[-1]:
                content = [{"type": "text", "text": msg["content"]}]
                for img in images:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img
                        }
                    })
                converted.append({"role": "user", "content": content})
            else:
                converted.append({"role": msg["role"], "content": msg["content"]})
        return system.strip(), converted

    def chat(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096, images: list[str] = None) -> str:
        system, msgs = self._convert_messages(messages, images)
        body = {
            "model": self.model,
            "messages": msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            body["system"] = system

        response = self.client.post(
            f"{self.base_url}/messages",
            headers=self._headers(),
            json=body,
        )
        response.raise_for_status()
        data = response.json()
        u = data.get("usage", {})
        self.last_usage = {
            "input": u.get("input_tokens", 0),
            "output": u.get("output_tokens", 0),
            "total": u.get("input_tokens", 0) + u.get("output_tokens", 0),
            "cache_read": u.get("cache_read_input_tokens", 0),
        }
        return "".join(block["text"] for block in data["content"] if block["type"] == "text")

    async def chat_async(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096, images: list[str] = None) -> str:
        system, msgs = self._convert_messages(messages, images)
        body = {
            "model": self.model,
            "messages": msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            body["system"] = system

        response = await self.async_client.post(
            f"{self.base_url}/messages",
            headers=self._headers(),
            json=body,
        )
        response.raise_for_status()
        data = response.json()
        u = data.get("usage", {})
        self.last_usage = {
            "input": u.get("input_tokens", 0),
            "output": u.get("output_tokens", 0),
            "total": u.get("input_tokens", 0) + u.get("output_tokens", 0),
            "cache_read": u.get("cache_read_input_tokens", 0),
        }
        return "".join(block["text"] for block in data["content"] if block["type"] == "text")

    def chat_stream(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096) -> Generator[str, None, None]:
        system, msgs = self._convert_messages(messages)
        body = {
            "model": self.model,
            "messages": msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if system:
            body["system"] = system

        with self.client.stream(
            "POST",
            f"{self.base_url}/messages",
            headers=self._headers(),
            json=body,
        ) as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        if data.get("type") == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta" and delta.get("text"):
                                yield delta["text"]
                    except (json.JSONDecodeError, KeyError):
                        continue

    async def chat_stream_async(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096):
        system, msgs = self._convert_messages(messages)
        body = {
            "model": self.model,
            "messages": msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if system:
            body["system"] = system

        async with self.async_client.stream(
            "POST",
            f"{self.base_url}/messages",
            headers=self._headers(),
            json=body,
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        if data.get("type") == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta" and delta.get("text"):
                                yield delta["text"]
                    except (json.JSONDecodeError, KeyError):
                        continue

    def list_models(self) -> list[str]:
        return self.MODELS

    def verify(self) -> bool:
        return bool(self.api_key)

    def close(self):
        self.client.close()


class GeminiProvider(LLMProvider):
    """Google Gemini API provider."""

    name = "gemini"

    MODELS = [
        "gemini-2.0-flash-exp", "gemini-2.0-flash-lite-preview-02-05",
        "gemini-2.0-pro-exp-02-05", "gemini-1.5-pro", "gemini-1.5-flash",
    ]

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        self.client = httpx.Client(timeout=120.0)
        self.async_client = httpx.AsyncClient(timeout=120.0)

    def _convert_messages(self, messages: list[dict], images: list[str] = None) -> tuple[str, list[dict]]:
        """Convert OpenAI-style messages to Gemini format."""
        system = ""
        contents = []
        for msg in messages:
            if msg["role"] == "system":
                system += msg["content"] + "\n"
            elif msg["role"] == "user":
                parts = [{"text": msg["content"]}]
                if images and msg == messages[-1]:
                    for img in images:
                        parts.append({
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": img
                            }
                        })
                contents.append({"role": "user", "parts": parts})
            elif msg["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": msg["content"]}]})
        return system.strip(), contents

    def chat(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096, images: list[str] = None) -> str:
        system, contents = self._convert_messages(messages, images)
        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        response = self.client.post(
            f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}",
            json=body,
        )
        response.raise_for_status()
        data = response.json()
        meta = data.get("usageMetadata", {})
        self.last_usage = {
            "input": meta.get("promptTokenCount", 0),
            "output": meta.get("candidatesTokenCount", 0),
            "total": meta.get("totalTokenCount", 0),
        }
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)
        return "No response generated."

    async def chat_async(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096, images: list[str] = None) -> str:
        system, contents = self._convert_messages(messages, images)
        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        response = await self.async_client.post(
            f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}",
            json=body,
        )
        response.raise_for_status()
        data = response.json()
        meta = data.get("usageMetadata", {})
        self.last_usage = {
            "input": meta.get("promptTokenCount", 0),
            "output": meta.get("candidatesTokenCount", 0),
            "total": meta.get("totalTokenCount", 0),
        }
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)
        return "No response generated."

    def chat_stream(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096) -> Generator[str, None, None]:
        system, contents = self._convert_messages(messages)
        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        with self.client.stream(
            "POST",
            f"{self.base_url}/models/{self.model}:streamGenerateContent?alt=sse&key={self.api_key}",
            json=body,
        ) as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            for p in parts:
                                if p.get("text"):
                                    yield p["text"]
                    except (json.JSONDecodeError, KeyError):
                        continue

    async def chat_stream_async(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096):
        system, contents = self._convert_messages(messages)
        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        async with self.async_client.stream(
            "POST",
            f"{self.base_url}/models/{self.model}:streamGenerateContent?alt=sse&key={self.api_key}",
            json=body,
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            for p in parts:
                                if p.get("text"):
                                    yield p["text"]
                    except (json.JSONDecodeError, KeyError):
                        continue

    def list_models(self) -> list[str]:
        return self.MODELS

    def verify(self) -> bool:
        return bool(self.api_key)

    def close(self):
        self.client.close()


class MiniMaxProvider(OpenAIProvider):
    """MiniMax API provider."""
    name = "minimax"
    def __init__(self, api_key: str, model: str = "abab6.5s-chat"):
        super().__init__(api_key, model, base_url="https://api.minimax.chat/v1")

class GLMProvider(OpenAIProvider):
    """GLM (Zhipu AI) API provider."""
    name = "glm"
    def __init__(self, api_key: str, model: str = "glm-4"):
        super().__init__(api_key, model, base_url="https://open.bigmodel.cn/api/paas/v4")

class HuggingFaceProvider(OpenAIProvider):
    """HuggingFace Inference API provider."""
    name = "huggingface"
    def __init__(self, api_key: str, model: str = "meta-llama/Llama-2-70b-chat-hf"):
        super().__init__(api_key, model, base_url="https://api-inference.huggingface.co/v1")

class VercelProvider(OpenAIProvider):
    """Vercel AI Gateway provider."""
    name = "vercel"
    def __init__(self, api_key: str, model: str = ""):
        # User provides full path as model name
        super().__init__(api_key, model, base_url="https://gateway.ai.vercel.com/v1")


# ── Provider Registry ──

PROVIDER_MODELS = {
    "ollama": {"prefix": "", "description": "Local (Ollama)"},
    "openai": {"prefix": "gpt-|o1|o3", "description": "OpenAI"},
    "anthropic": {"prefix": "claude-", "description": "Anthropic"},
    "gemini": {"prefix": "gemini-", "description": "Google Gemini"},
    "deepseek": {"prefix": "deepseek-", "description": "DeepSeek"},
    "openrouter": {"prefix": "openrouter/", "description": "OpenRouter"},
    "xai": {"prefix": "grok-", "description": "xAI (Grok)"},
    "mistral": {"prefix": "mistral-", "description": "Mistral"},
    "perplexity": {"prefix": "sonar", "description": "Perplexity"},
    "minimax": {"prefix": "abab", "description": "MiniMax"},
    "glm": {"prefix": "glm-", "description": "GLM (Zhipu)"},
}


def detect_provider(model_name: str) -> str:
    """Detect which provider a model belongs to based on name."""
    model_lower = model_name.lower()
    if model_lower.startswith("gpt-") or model_lower.startswith(("o1", "o3")):
        return "openai"
    if model_lower.startswith("claude-"):
        return "anthropic"
    if model_lower.startswith("gemini-"):
        return "gemini"
    if model_lower.startswith("deepseek-"):
        return "deepseek"
    if "/" in model_name: # OpenRouter models usually have a slash
        return "openrouter"
    if model_lower.startswith("grok-"):
        return "xai"
    if model_lower.startswith("mistral-"):
        return "mistral"
    if "sonar" in model_lower:
        return "perplexity"
    if model_lower.startswith("abab"):
        return "minimax"
    if model_lower.startswith("glm-"):
        return "glm"
    return "ollama"


def create_provider(
    provider_name: str,
    model: str = "",
    api_key: str = "",
    ollama_host: str = "http://localhost:11434",
    context_window: int = 8192,
) -> LLMProvider:
    """Create a provider instance."""
    if provider_name == "openai":
        return OpenAIProvider(api_key=api_key, model=model or "gpt-4o")
    elif provider_name == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model or "claude-3-5-sonnet-20241022")
    elif provider_name == "gemini":
        return GeminiProvider(api_key=api_key, model=model or "gemini-2.0-flash")
    elif provider_name == "deepseek":
        return DeepSeekProvider(api_key=api_key, model=model or "deepseek-chat")
    elif provider_name == "openrouter":
        return OpenRouterProvider(api_key=api_key, model=model or "google/gemini-2.0-flash-001")
    elif provider_name == "xai":
        return XAIProvider(api_key=api_key, model=model or "grok-beta")
    elif provider_name == "perplexity":
        return PerplexityProvider(api_key=api_key, model=model or "llama-3.1-sonar-large-128k-online")
    elif provider_name == "mistral":
        return MistralProvider(api_key=api_key, model=model or "mistral-large-latest")
    elif provider_name == "minimax":
        return MiniMaxProvider(api_key=api_key, model=model or "abab6.5s-chat")
    elif provider_name == "glm":
        return GLMProvider(api_key=api_key, model=model or "glm-4")
    elif provider_name == "huggingface":
        return HuggingFaceProvider(api_key=api_key, model=model or "meta-llama/Llama-2-70b-chat-hf")
    elif provider_name == "vercel":
        return VercelProvider(api_key=api_key, model=model)
    else:
        return OllamaProvider(host=ollama_host, model=model or "qwen2.5:7b", context_window=context_window)
