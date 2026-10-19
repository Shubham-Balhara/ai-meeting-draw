from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import io
import json
import logging
from pathlib import Path
import random
import wave

from .config import MeetingSettings


SAMPLE_RATE = 16_000
logger = logging.getLogger("ai_meeting_draw.meeting.stt")


@dataclass(frozen=True, slots=True)
class STTResult:
    text: str
    language: str | None = None
    confidence: float | None = None
    average_log_probability: float | None = None
    no_speech_probability: float | None = None
    metadata: dict = field(default_factory=dict)


def pcm_to_wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(SAMPLE_RATE); wav.writeframes(pcm)
    return output.getvalue()


class Transcriber:
    name = "unknown"
    model = "unknown"
    async def transcribe(self, pcm: bytes) -> STTResult: raise NotImplementedError
    async def close(self) -> None: return None


class WhisperXTranscriber(Transcriber):
    name = "whisperx"

    def __init__(self, settings: MeetingSettings):
        python = Path(settings.whisperx_python).expanduser()
        if not python.is_file():
            raise ValueError(f"WHISPERX_PYTHON does not exist: {python}")
        self.model = settings.whisper_model
        self._python, self._settings = str(python), settings
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def _start(self) -> None:
        if self._process and self._process.returncode is None: return
        worker = Path(__file__).with_name("whisperx_worker.py")
        args = [self._python, str(worker), "--model", self.model, "--device", self._settings.whisper_device,
                "--compute-type", self._settings.whisper_compute_type, "--batch-size", str(self._settings.whisperx_batch_size),
                "--vad-method", self._settings.whisperx_vad_method,
                "--vad-onset", str(self._settings.whisperx_vad_onset),
                "--vad-offset", str(self._settings.whisperx_vad_offset)]
        if self._settings.whisper_language: args += ["--language", self._settings.whisper_language]
        if self._settings.stt_initial_prompt: args += ["--initial-prompt", self._settings.stt_initial_prompt]
        if self._settings.stt_hotwords: args += ["--hotwords", self._settings.stt_hotwords]
        logger.info(
            "Starting WhisperX worker model=%s device=%s compute_type=%s vad=%s",
            self.model,
            self._settings.whisper_device,
            self._settings.whisper_compute_type,
            self._settings.whisperx_vad_method,
        )
        self._process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        assert self._process.stdout
        response = await self._process.stdout.readline()
        if not response or json.loads(response).get("type") != "ready":
            raise RuntimeError("WhisperX worker failed to start")
        logger.info("WhisperX worker ready pid=%s", self._process.pid)

    async def transcribe(self, pcm: bytes) -> STTResult:
        async with self._lock:
            await self._start()
            logger.debug("Sending PCM to WhisperX bytes=%d", len(pcm))
            assert self._process and self._process.stdin and self._process.stdout
            self._process.stdin.write(json.dumps({"audio": base64.b64encode(pcm).decode()}).encode() + b"\n")
            await self._process.stdin.drain()
            line = await self._process.stdout.readline()
            if not line: raise RuntimeError("WhisperX worker exited unexpectedly")
            response = json.loads(line)
            if response.get("error"): raise RuntimeError(response["error"])
            text = response.get("text", "").strip()
            logger.debug("WhisperX response characters=%d", len(text))
            return STTResult(
                text=text, language=response.get("language"), confidence=response.get("confidence"),
                average_log_probability=response.get("average_log_probability"),
                no_speech_probability=response.get("no_speech_probability"),
                metadata={"segment_count": response.get("segment_count", 0)},
            )

    async def close(self) -> None:
        if self._process and self._process.stdin and not self._process.stdin.is_closing():
            self._process.stdin.close(); await self._process.stdin.wait_closed()
            try: await asyncio.wait_for(self._process.wait(), 5)
            except asyncio.TimeoutError: self._process.kill(); await self._process.wait()
            logger.info("WhisperX worker stopped")


class GeminiSTTTranscriber(Transcriber):
    name = "gemini"

    def __init__(self, settings: MeetingSettings):
        from google import genai
        self.model = settings.gemini_stt_model
        self._settings = settings
        self._client = (genai.Client(vertexai=True, project=settings.firestore_project_id,
                                    location=settings.google_cloud_location)
                        if settings.gemini_backend == "vertex"
                        else genai.Client(api_key=settings.gemini_api_key))
        self._lock = asyncio.Lock()
        logger.info("Gemini STT client configured backend=%s location=%s",
                    settings.gemini_backend,settings.google_cloud_location)

    async def transcribe(self, pcm: bytes) -> STTResult:
        from google.genai import types
        async with self._lock:
            for attempt in range(3):
                try:
                    logger.debug("Sending PCM to Gemini STT bytes=%d attempt=%d", len(pcm), attempt + 1)
                    response = await self._client.aio.models.generate_content(
                        model=self.model,
                        contents=[f"Transcribe this speech verbatim. Return only the transcript; return an empty string for silence. The domain is software system design. Preserve technical terms and names. Relevant vocabulary: {self._settings.stt_hotwords}",
                                  types.Part.from_bytes(data=pcm_to_wav(pcm), mime_type="audio/wav")],
                    )
                    text = (response.text or "").strip()
                    logger.debug("Gemini STT response characters=%d", len(text))
                    usage = getattr(response, "usage_metadata", None)
                    return STTResult(text=text, language=self._settings.whisper_language, metadata={
                        "prompt_token_count": getattr(usage, "prompt_token_count", None),
                        "candidates_token_count": getattr(usage, "candidates_token_count", None),
                    })
                except Exception as exc:
                    if getattr(exc, "code", None) not in {408, 429, 500, 502, 503, 504} or attempt == 2: raise
                    logger.warning("Retrying Gemini STT attempt=%d error=%s", attempt + 1, exc)
                    await asyncio.sleep((2 ** attempt) + random.random())
        return STTResult(text="")

    async def close(self) -> None:
        await self._client.aio.aclose(); self._client.close()


def create_transcriber(settings: MeetingSettings) -> Transcriber:
    settings.validate()
    transcriber = GeminiSTTTranscriber(settings) if settings.stt_provider == "gemini" else WhisperXTranscriber(settings)
    logger.info("STT provider configured provider=%s model=%s", transcriber.name, transcriber.model)
    return transcriber
