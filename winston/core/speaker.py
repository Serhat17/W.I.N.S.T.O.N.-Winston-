"""
Speaker module - Text-to-Speech.
Supports ElevenLabs (cloud, best quality), Piper (local, fast), and macOS say (fallback).

Priority (engine=auto):
  1. ElevenLabs  — if elevenlabs_api_key is set
  2. Piper       — if piper binary or model is available
  3. macOS say   — always available on Mac
"""

import io
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from winston.config import TTSConfig

logger = logging.getLogger("winston.speaker")

# Piper model download URLs (HuggingFace)
PIPER_MODEL_BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main"
)
PIPER_MODELS = {
    "en_US-lessac-high": "en/en_US/lessac/high/en_US-lessac-high",
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium",
    "en_US-ryan-high": "en/en_US/ryan/high/en_US-ryan-high",
    "en_GB-alan-high": "en/en_GB/alan/high/en_GB-alan-high",
    "de_DE-thorsten-high": "de/de_DE/thorsten/high/de_DE-thorsten-high",
}


class Speaker:
    """
    Text-to-speech engine with multiple backends.

    Engines:
      - elevenlabs : Cloud, highest quality (requires API key)
      - piper      : Local, fast, good quality (auto-downloads model)
      - macos      : macOS built-in 'say' command (fallback)
      - auto       : Picks the best available engine automatically
    """

    def __init__(self, config: TTSConfig):
        self.config = config
        self._engine: Optional[str] = None

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize(self):
        """Detect and initialize the best available TTS engine."""
        engine = self.config.engine.lower()

        if engine == "elevenlabs":
            self._init_elevenlabs()
        elif engine == "piper":
            self._init_piper()
        elif engine == "coqui":
            self._init_coqui()
        elif engine == "bark":
            self._init_bark()
        elif engine in ("macos", "say"):
            self._engine = "macos_say"
        elif engine == "auto":
            self._auto_detect()
        else:
            logger.warning(f"Unknown TTS engine '{engine}', falling back to auto-detect.")
            self._auto_detect()

    def _auto_detect(self):
        """Pick the best available engine automatically."""
        if self.config.elevenlabs_api_key:
            self._init_elevenlabs()
            if self._engine == "elevenlabs":
                return
        self._init_piper()
        if self._engine in ("piper", "piper_binary"):
            return
        self._engine = "macos_say"
        logger.info("TTS: using macOS say (fallback)")

    def _init_elevenlabs(self):
        """Initialize ElevenLabs cloud TTS."""
        try:
            import httpx  # already a project dependency
            # Quick auth check
            if not self.config.elevenlabs_api_key:
                logger.warning("ElevenLabs: no API key set (tts.elevenlabs_api_key)")
                return
            self._engine = "elevenlabs"
            logger.info("TTS: ElevenLabs initialized ✓")
        except Exception as e:
            logger.warning(f"ElevenLabs init failed: {e}")

    def _init_piper(self):
        """Initialize Piper TTS (tries Python package, then system binary)."""
        # Try Python package first
        try:
            import piper as _piper  # noqa: F401
            self._engine = "piper"
            logger.info("TTS: Piper (Python package) initialized ✓")
            return
        except ImportError:
            pass

        # Try system binary
        result = subprocess.run(["which", "piper"], capture_output=True, text=True)
        if result.returncode == 0:
            model_path = self._ensure_piper_model()
            if model_path:
                self._piper_model_path = model_path
                self._engine = "piper_binary"
                logger.info(f"TTS: Piper (binary) initialized with {model_path} ✓")
                return

        logger.info("TTS: Piper not available, will use macOS say")

    def _ensure_piper_model(self) -> Optional[str]:
        """Download the Piper model if it isn't cached yet. Returns path to .onnx file."""
        if self.config.piper_model_path:
            p = Path(self.config.piper_model_path)
            if p.exists():
                return str(p)

        model_key = self.config.piper_model
        if model_key not in PIPER_MODELS:
            logger.warning(f"Unknown piper model '{model_key}'. Check config.")
            return None

        voices_dir = Path.home() / ".winston" / "voices"
        voices_dir.mkdir(parents=True, exist_ok=True)

        onnx = voices_dir / f"{model_key}.onnx"
        json_ = voices_dir / f"{model_key}.onnx.json"

        base_url = f"{PIPER_MODEL_BASE}/{PIPER_MODELS[model_key]}"

        if not onnx.exists():
            logger.info(f"Downloading Piper model: {model_key} …")
            try:
                import urllib.request
                urllib.request.urlretrieve(f"{base_url}.onnx", onnx)
                urllib.request.urlretrieve(f"{base_url}.onnx.json", json_)
                logger.info(f"Piper model downloaded to {onnx}")
            except Exception as e:
                logger.warning(f"Could not download Piper model: {e}")
                return None

        return str(onnx)

    def _init_coqui(self):
        try:
            from TTS.api import TTS
            self._tts_model = TTS(model_name=self.config.coqui_model, progress_bar=False)
            self._engine = "coqui"
        except ImportError:
            logger.warning("Coqui TTS not installed. Run: pip install TTS")
            self._auto_detect()

    def _init_bark(self):
        try:
            from bark import preload_models
            preload_models()
            self._engine = "bark"
        except ImportError:
            logger.warning("Bark not installed.")
            self._auto_detect()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def speak(self, text: str, block: bool = True):
        """Convert text to speech and play it."""
        if not self._engine:
            self.initialize()

        if not text or not text.strip():
            return

        text = self._clean_text(text)
        if not text:
            return

        logger.debug(f"Speaking [{self._engine}]: '{text[:60]}...'")

        try:
            dispatch = {
                "elevenlabs": lambda: self._speak_elevenlabs(text),
                "piper": lambda: self._speak_piper_pkg(text),
                "piper_binary": lambda: self._speak_piper_binary(text),
                "coqui": lambda: self._speak_coqui(text),
                "bark": lambda: self._speak_bark(text),
                "macos_say": lambda: self._speak_macos(text, block),
            }
            fn = dispatch.get(self._engine)
            if fn:
                fn()
            else:
                print(f"  [WINSTON TTS]: {text}")
        except Exception as e:
            logger.error(f"TTS error ({self._engine}): {e}")
            # Fallback
            try:
                self._speak_macos(text, block)
            except Exception:
                print(f"  [WINSTON]: {text}")

    def stop(self):
        """Stop any ongoing speech."""
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    @property
    def active_engine(self) -> str:
        if not self._engine:
            self.initialize()
        return self._engine or "none"

    # ------------------------------------------------------------------
    # ElevenLabs
    # ------------------------------------------------------------------

    def _speak_elevenlabs(self, text: str):
        """Stream audio from ElevenLabs API and play via afplay."""
        import httpx

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.config.elevenlabs_voice_id}"
        headers = {
            "xi-api-key": self.config.elevenlabs_api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": self.config.elevenlabs_model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }

        with httpx.Client(timeout=30) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()

        # Write to temp file and play
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(resp.content)
            tmp_path = f.name

        try:
            subprocess.run(["afplay", tmp_path], check=True, capture_output=True)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Piper
    # ------------------------------------------------------------------

    def _speak_piper_pkg(self, text: str):
        """Speak with piper-tts Python package."""
        import piper
        import sounddevice as sd

        model_path = self._ensure_piper_model()
        voice = piper.PiperVoice.load(model_path)
        for chunk in voice.synthesize(text):
            arr = bytearray(chunk)
            sd.play(arr, samplerate=voice.config.sample_rate)
            sd.wait()

    def _speak_piper_binary(self, text: str):
        """Speak with system piper binary."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            result = subprocess.run(
                ["piper", "--model", self._piper_model_path, "--output_file", tmp.name],
                input=text,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                subprocess.run(["afplay", tmp.name], capture_output=True)
            Path(tmp.name).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Coqui / Bark
    # ------------------------------------------------------------------

    def _speak_coqui(self, text: str):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            self._tts_model.tts_to_file(text=text, file_path=tmp.name)
            subprocess.run(["afplay", tmp.name], capture_output=True)
            Path(tmp.name).unlink(missing_ok=True)

    def _speak_bark(self, text: str):
        from bark import SAMPLE_RATE, generate_audio
        import sounddevice as sd
        sd.play(generate_audio(text), samplerate=SAMPLE_RATE)
        sd.wait()

    # ------------------------------------------------------------------
    # macOS say (fallback)
    # ------------------------------------------------------------------

    def _speak_macos(self, text: str, block: bool = True):
        """macOS built-in TTS — uses Samantha (best quality built-in voice)."""
        cmd = ["say", "-v", "Samantha", "-r", str(int(180 * self.config.piper_speed)), text]
        if block:
            subprocess.run(cmd, capture_output=True)
        else:
            subprocess.Popen(cmd)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clean_text(self, text: str) -> str:
        """Strip markdown, code blocks, JSON etc. before sending to TTS."""
        import re
        text = re.sub(r"```[\s\S]*?```", " ", text)
        text = re.sub(r"`([^`]*)`", r"\1", text)
        text = re.sub(r"[*_#>]", "", text)
        text = re.sub(r"\{[^}]*\}", "", text)
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
