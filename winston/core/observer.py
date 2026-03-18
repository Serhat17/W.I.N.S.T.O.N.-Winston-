"""
Observer module - Background audio capture and continuous transcription.
"""

import asyncio
import logging
import threading
import time
import wave
import tempfile
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger("winston.observer")

class Observer:
    """
    Observer runs in the background, continuously capturing audio from a specified input device
    (like BlackHole for system audio, or microphone), chunking it, and transcribing it.
    """

    def __init__(self, config, listener, callback: Callable[[str], None]):
        """
        Args:
            config: Winston config object.
            listener: Initialized Listener object for transcription (`transcribe_file`).
            callback: Function to call with the transcribed text of each chunk.
        """
        self.config = config
        self.listener = listener
        self.callback = callback
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # Audio capturing parameters
        self.chunk_duration = 60  # seconds per chunk
        self.sample_rate = 16000
        self.channels = 1
        self.chunk_size = 1024
        
        # Device selection (configured via config later, default to None = default input)
        self.device_index = None

    def start(self):
        """Start the observer thread."""
        if self._running:
            return
            
        try:
            import pyaudio
            self._pyaudio = pyaudio.PyAudio()
        except ImportError:
            logger.error("PyAudio not installed. Observer cannot start.")
            return

        # Attempt to find the configured observer device (e.g. BlackHole)
        self.device_index = self._find_device("BlackHole")
        if self.device_index is not None:
            logger.info(f"Observer using input device index {self.device_index} (BlackHole expected)")
        else:
            logger.info("Observer using default input device.")

        self._running = True
        self._thread = threading.Thread(target=self._record_and_process_loop, daemon=True)
        self._thread.start()
        logger.info("Observer Mode active. Listening in the background...")

    def stop(self):
        """Stop the observer thread."""
        if not self._running:
            return
        
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            
        if hasattr(self, '_pyaudio') and self._pyaudio:
            self._pyaudio.terminate()
            
        logger.info("Observer Mode stopped.")

    def _find_device(self, keyword: str) -> Optional[int]:
        """Find an audio input device containing the keyword."""
        count = self._pyaudio.get_device_count()
        for i in range(count):
            try:
                info = self._pyaudio.get_device_info_by_index(i)
                if info["maxInputChannels"] > 0 and keyword.lower() in info["name"].lower():
                    return i
            except Exception:
                continue
        return None

    def _record_and_process_loop(self):
        """Continuously records audio in chunks and processes them."""
        import pyaudio
        
        while self._running:
            audio_frames = []
            stream = None
            try:
                stream = self._pyaudio.open(
                    format=pyaudio.paInt16,
                    channels=self.channels,
                    rate=self.sample_rate,
                    input=True,
                    input_device_index=self.device_index,
                    frames_per_buffer=self.chunk_size
                )
                
                # Calculate how many chunks make up self.chunk_duration
                num_chunks = int((self.sample_rate / self.chunk_size) * self.chunk_duration)
                
                logger.debug(f"Observer recording {self.chunk_duration}s chunk...")
                for _ in range(num_chunks):
                    if not self._running:
                        break
                    data = stream.read(self.chunk_size, exception_on_overflow=False)
                    audio_frames.append(data)
                    
            except Exception as e:
                logger.error(f"Observer audio capture error: {e}")
                time.sleep(5)  # Wait before retrying
                continue
            finally:
                if stream:
                    stream.stop_stream()
                    stream.close()

            if not audio_frames:
                continue

            # Process the chunk
            self._process_chunk(audio_frames)

    def _process_chunk(self, frames: list[bytes]):
        """Save chunk to a temp file, transcribe it, and pass to callback."""
        import pyaudio
        
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
            
        try:
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(self._pyaudio.get_sample_size(pyaudio.paInt16))
                wf.setframerate(self.sample_rate)
                wf.writeframes(b"".join(frames))
                
            # Transcribe
            logger.debug(f"Observer transcribing {self.chunk_duration}s chunk...")
            text = self.listener.transcribe_file(wav_path)
            
            if text and text.strip():
                # Filter out very short noise artifacts
                if len(text.strip()) > 10:
                    logger.info(f"Observer transcribed: {text[:50]}...")
                    self.callback(text.strip())
                else:
                    logger.debug("Observer ignored short/empty transcription.")
                    
        except Exception as e:
            logger.error(f"Observer transcription error: {e}")
        finally:
            Path(wav_path).unlink(missing_ok=True)
