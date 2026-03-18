"""Setup for W.I.N.S.T.O.N."""

from setuptools import setup, find_packages

setup(
    name="winston-ai",
    version="1.0.0",
    description="W.I.N.S.T.O.N. - Wildly Intelligent Network System for Task Operations and Navigation",
    author="Serhat Bilge",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "httpx>=0.25.0",
        "pyyaml>=6.0",
        "numpy>=1.24.0",
    ],
    extras_require={
        "voice": [
            "faster-whisper>=1.0.0",
            "pyaudio>=0.2.13",
            "sounddevice>=0.4.6",
            "soundfile>=0.12.1",
            "openwakeword>=0.6.0",
        ],
        "tts": [
            "piper-tts>=1.2.0",
        ],
        "memory": [
            "chromadb>=0.4.0",
        ],
        "skills": [
            "duckduckgo-search>=4.0",
            "psutil>=5.9.0",
        ],
        "server": [
            "fastapi>=0.110.0",
            "uvicorn[standard]>=0.27.0",
            "websockets>=12.0",
        ],
        "channels": [
            "python-telegram-bot>=21.0",
            "discord.py>=2.3.0",
        ],
        "scheduler": [
            "APScheduler>=3.10.0",
        ],
        "all": [
            "faster-whisper>=1.0.0",
            "pyaudio>=0.2.13",
            "sounddevice>=0.4.6",
            "soundfile>=0.12.1",
            "openwakeword>=0.6.0",
            "piper-tts>=1.2.0",
            "chromadb>=0.4.0",
            "duckduckgo-search>=4.0",
            "psutil>=5.9.0",
            "fastapi>=0.110.0",
            "uvicorn[standard]>=0.27.0",
            "websockets>=12.0",
            "python-telegram-bot>=21.0",
            "discord.py>=2.3.0",
            "APScheduler>=3.10.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "winston=winston.main:main",
        ],
    },
)
