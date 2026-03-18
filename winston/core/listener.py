"""
Listener module - Speech-to-Text using faster-whisper.
Captures audio from microphone and transcribes it to text.
Supports wake word detection for hands-free activation.
"""

import io
import logging
import tempfile
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from winston.config import WhisperConfig, WakeWordConfig

logger = logging.getLogger("winston.listener")

# Audio constants
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 1024
RECORD_SECONDS_MAX = 30  # Maximum recording duration
SILENCE_THRESHOLD = 500  # Amplitude threshold for silence detection
SILENCE_DURATION = 2.0  # Seconds of silence before stopping


class Listener:
    """
    Captures audio from the microphone and transcribes it using faster-whisper.
    Supports continuous listening with wake word detection.
    """

    def __init__(self, whisper_config: WhisperConfig, wake_config: WakeWordConfig = None):
        self.whisper_config = whisper_config
        self.wake_config = wake_config
        self.whisper_model = None
        self.wake_word_model = None
        self._is_listening = False

    def initialize(self):
        """Load the Whisper model (lazy loading to save memory)."""
        if self.whisper_model is None:
            try:
                from faster_whisper import WhisperModel

                device = self.whisper_config.device
                if device == "auto":
                    try:
                        import torch
                        device = "cuda" if torch.cuda.is_available() else "cpu"
                    except ImportError:
                        device = "cpu"

                logger.info(
                    f"Loading Whisper model '{self.whisper_config.model_size}' "
                    f"on {device} with {self.whisper_config.compute_type}..."
                )
                self.whisper_model = WhisperModel(
                    self.whisper_config.model_size,
                    device=device,
                    compute_type=self.whisper_config.compute_type,
                )
                logger.info("Whisper model loaded successfully.")
            except ImportError:
                logger.error(
                    "faster-whisper not installed! Install with: pip install faster-whisper"
                )
                raise
            except Exception as e:
                logger.error(f"Failed to load Whisper model: {e}")
                raise

    def initialize_wake_word(self):
        """Initialize wake word detection model."""
        if self.wake_config and self.wake_config.enabled:
            try:
                from openwakeword.model import Model as OWWModel

                self.wake_word_model = OWWModel(
                    wakeword_models=[self.wake_config.wake_word],
                    inference_framework="onnx",
                )
                logger.info(
                    f"Wake word model loaded for '{self.wake_config.wake_word}'"
                )
            except ImportError:
                logger.warning(
                    "openwakeword not installed. Wake word detection disabled. "
                    "Install with: pip install openwakeword"
                )
                self.wake_config.enabled = False
            except Exception as e:
                logger.warning(f"Failed to load wake word model: {e}")
                self.wake_config.enabled = False

    def listen(self, timeout: float = None) -> Optional[str]:
        """
        Listen to the microphone and return transcribed text.

        Args:
            timeout: Maximum seconds to listen. None = listen until silence.

        Returns:
            Transcribed text, or None if nothing was captured.
        """
        self.initialize()

        try:
            import pyaudio
        except ImportError:
            logger.error("PyAudio not installed! Install with: pip install pyaudio")
            raise

        audio = pyaudio.PyAudio()
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
        )

        logger.info("Listening... (speak now)")
        frames = []
        silence_frames = 0
        max_silence_frames = int(SILENCE_DURATION * SAMPLE_RATE / CHUNK_SIZE)
        max_total_frames = int((timeout or RECORD_SECONDS_MAX) * SAMPLE_RATE / CHUNK_SIZE)

        self._is_listening = True
        try:
            for i in range(max_total_frames):
                if not self._is_listening:
                    break

                data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                frames.append(data)

                # Check for silence (simple amplitude-based VAD)
                audio_data = np.frombuffer(data, dtype=np.int16)
                amplitude = np.abs(audio_data).mean()

                if amplitude < SILENCE_THRESHOLD:
                    silence_frames += 1
                    if silence_frames > max_silence_frames and len(frames) > 10:
                        logger.debug("Silence detected, stopping recording.")
                        break
                else:
                    silence_frames = 0

        finally:
            stream.stop_stream()
            stream.close()
            audio.terminate()
            self._is_listening = False

        if not frames or len(frames) < 5:
            return None

        # Save to temporary WAV file
        audio_data = b"".join(frames)
        return self._transcribe_audio(audio_data)

    def listen_for_wake_word(self, callback):
        """
        Continuously listen for the wake word, then call callback with transcribed speech.

        Args:
            callback: Function to call with transcribed text when wake word is detected.
        """
        self.initialize()
        self.initialize_wake_word()

        if not self.wake_config or not self.wake_config.enabled:
            logger.warning("Wake word not available, falling back to push-to-talk")
            return

        try:
            import pyaudio
        except ImportError:
            logger.error("PyAudio not installed!")
            return

        audio = pyaudio.PyAudio()
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
        )

        logger.info(f"Listening for wake word '{self.wake_config.wake_word}'...")
        self._is_listening = True

        try:
            while self._is_listening:
                data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                audio_array = np.frombuffer(data, dtype=np.int16)

                # Feed to wake word model
                prediction = self.wake_word_model.predict(audio_array)

                # Check if wake word was detected
                for model_name, score in prediction.items():
                    if score > self.wake_config.threshold:
                        logger.info(f"Wake word detected! (score: {score:.2f})")
                        # Now listen for the actual command
                        text = self.listen()
                        if text:
                            callback(text)
                        logger.info(
                            f"Listening for wake word '{self.wake_config.wake_word}'..."
                        )
        finally:
            stream.stop_stream()
            stream.close()
            audio.terminate()

    def _transcribe_audio(self, audio_data: bytes) -> Optional[str]:
        """Transcribe raw audio bytes using faster-whisper."""
        # Write to temp WAV file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)  # 16-bit = 2 bytes
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio_data)

            tmp_path = tmp.name

        try:
            segments, info = self.whisper_model.transcribe(
                tmp_path,
                beam_size=self.whisper_config.beam_size,
                language=self.whisper_config.language,
                vad_filter=self.whisper_config.vad_filter,
            )

            text = " ".join(segment.text.strip() for segment in segments)
            text = text.strip()

            if text:
                logger.info(f"Transcribed: '{text}'")
                return text
            return None

        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return None
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def transcribe_file(self, audio_path: str) -> Optional[str]:
        """Transcribe an audio file directly."""
        self.initialize()
        try:
            segments, info = self.whisper_model.transcribe(
                audio_path,
                beam_size=self.whisper_config.beam_size,
                language=self.whisper_config.language,
                vad_filter=self.whisper_config.vad_filter,
            )
            text = " ".join(segment.text.strip() for segment in segments)
            return text.strip() if text.strip() else None
        except Exception as e:
            logger.error(f"File transcription error: {e}")
            return None

    def stop(self):
        """Stop listening."""
        self._is_listening = False
