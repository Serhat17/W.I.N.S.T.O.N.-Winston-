"""
Image Generation Skill for W.I.N.S.T.O.N.
Supports OpenAI (DALL-E 3), Stability AI, and Pollinations AI (Free).
"""

import base64
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx
from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.image_gen")

class ImageGenerationSkill(BaseSkill):
    name = "image_gen"
    description = "Generate an image from a text description (prompt)."
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Descriptive prompt for the image (e.g., 'a cat in a space suit').",
            },
            "provider": {
                "type": "string",
                "enum": ["openai", "stability", "pollinations"],
                "description": "Optional: Specific provider to use.",
            },
            "model": {
                "type": "string",
                "description": "Optional: Specific model ID (e.g., 'dall-e-3' or 'stable-diffusion-xl-1024-v1-0').",
            }
        },
        "required": ["prompt"],
    }

    def __init__(self, config=None):
        super().__init__(config)
        providers = getattr(config, "providers", None)
        self.openai_key = getattr(providers, "openai_api_key", None)
        self.stability_key = getattr(providers, "stability_api_key", None)
        self.default_provider = getattr(config, "image_provider", "pollinations")
        self.default_model = getattr(config, "image_model", "dall-e-3")
        self.output_dir = Path.home() / ".winston" / "generated_images"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def execute(self, prompt: str, provider: str = None, model: str = None) -> SkillResult:
        provider = provider or self.default_provider
        model = model or self.default_model

        logger.info(f"Generating image with {provider}: {prompt[:50]}...")

        try:
            if provider == "openai":
                return self._gen_openai(prompt, model)
            elif provider == "stability":
                return self._gen_stability(prompt, model)
            else:
                result = self._gen_pollinations(prompt)
                if result.success:
                    return result
                # Fallback: if Pollinations fails, try OpenAI or Stability
                logger.warning(f"Pollinations failed, trying fallbacks...")
                if self.openai_key:
                    logger.info("Falling back to OpenAI for image generation")
                    return self._gen_openai(prompt, model or "dall-e-3")
                if self.stability_key:
                    logger.info("Falling back to Stability AI for image generation")
                    return self._gen_stability(prompt, model or "stable-diffusion-xl-1024-v1-0")
                return result  # Return the Pollinations error
        except Exception as e:
            logger.error(f"Image generation failed: {e}")
            return SkillResult(success=False, message=f"Failed to generate image: {str(e)}")

    def _gen_openai(self, prompt: str, model: str) -> SkillResult:
        if not self.openai_key:
            return SkillResult(success=False, message="OpenAI API key not configured for image generation.")

        url = "https://api.openai.com/v1/images/generations"
        headers = {"Authorization": f"Bearer {self.openai_key}", "Content-Type": "application/json"}
        payload = {
            "model": model or "dall-e-3",
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024",
            "response_format": "b64_json"
        }

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            b64_data = data["data"][0]["b64_json"]
            
            filepath = self._save_b64(b64_data, "openai")
            return SkillResult(
                success=True,
                message=f"Generated image with OpenAI: {filepath}",
                data={"image_b64": b64_data, "local_path": str(filepath)}
            )

    def _gen_stability(self, prompt: str, model: str) -> SkillResult:
        if not self.stability_key:
            return SkillResult(success=False, message="Stability AI API key not configured.")

        # Default model for Stability Core/Ultra/SDXL
        model_id = model or "stable-diffusion-xl-1024-v1-0"
        url = f"https://api.stability.ai/v1/generation/{model_id}/text-to-image"
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.stability_key}",
        }
        payload = {
            "text_prompts": [{"text": prompt}],
            "cfg_scale": 7,
            "height": 1024,
            "width": 1024,
            "samples": 1,
            "steps": 30,
        }

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            b64_data = data["artifacts"][0]["base64"]
            
            filepath = self._save_b64(b64_data, "stability")
            return SkillResult(
                success=True,
                message=f"Generated image with Stability AI: {filepath}",
                data={"image_b64": b64_data, "local_path": str(filepath)}
            )

    def _gen_pollinations(self, prompt: str) -> SkillResult:
        """Free image generation via Pollinations AI — uses streaming with retry."""
        import urllib.parse
        safe_prompt = urllib.parse.quote(prompt)
        
        # Try multiple URL variants (Pollinations is sometimes flaky with certain params)
        urls = [
            f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1024&height=1024",
            f"https://image.pollinations.ai/prompt/{safe_prompt}?width=512&height=512",
            f"https://image.pollinations.ai/prompt/{safe_prompt}",
        ]
        
        last_error = None
        for url in urls:
            try:
                with httpx.Client(timeout=120.0, follow_redirects=True) as client:
                    with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        img_bytes = b""
                        for chunk in resp.iter_bytes():
                            img_bytes += chunk
                
                if len(img_bytes) < 100:
                    last_error = "Empty response"
                    continue
                
                b64_data = base64.b64encode(img_bytes).decode("utf-8")
                filepath = self._save_bytes(img_bytes, "pollinations")
                return SkillResult(
                    success=True,
                    message=f"Generated image with Pollinations AI: {filepath}",
                    data={"image_b64": b64_data, "local_path": str(filepath)}
                )
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Pollinations attempt failed ({url[:60]}...): {e}")
                continue
        
        return SkillResult(success=False, message=f"All Pollinations attempts failed: {last_error}")

    def _save_b64(self, b64_data: str, provider: str) -> Path:
        img_bytes = base64.b64decode(b64_data)
        return self._save_bytes(img_bytes, provider)

    def _save_bytes(self, img_bytes: bytes, provider: str) -> Path:
        filename = f"{provider}_{int(time.time())}.png"
        filepath = self.output_dir / filename
        filepath.write_bytes(img_bytes)
        return filepath
