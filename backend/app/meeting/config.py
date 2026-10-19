from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _load_env(path: Path) -> None:
    """Load one env file without overriding an explicit process/primary value."""
    if not path.exists():
        return
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


# The unified repository-level .env is authoritative even when this module is
# imported directly (without app.config having run first).
_load_env(BACKEND_ROOT.parent / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MeetingSettings:
    stt_provider: str = field(default_factory=lambda: os.getenv("STT_PROVIDER", "whisper").lower())
    whisperx_python: str = field(default_factory=lambda: os.getenv(
        "WHISPERX_PYTHON", "python"
    ))
    whisper_model: str = field(default_factory=lambda: os.getenv("WHISPER_MODEL", "small"))
    whisper_device: str = field(default_factory=lambda: os.getenv("WHISPER_DEVICE", "cpu"))
    whisper_compute_type: str = field(default_factory=lambda: os.getenv("WHISPER_COMPUTE_TYPE", "int8"))
    whisper_language: str | None = field(default_factory=lambda: os.getenv("WHISPER_LANGUAGE", "en") or None)
    whisperx_batch_size: int = field(default_factory=lambda: int(os.getenv("WHISPERX_BATCH_SIZE", "4")))
    whisperx_vad_method: str = field(default_factory=lambda: os.getenv("WHISPERX_VAD_METHOD", "pyannote"))
    whisperx_vad_onset: float = field(default_factory=lambda: float(os.getenv("WHISPERX_VAD_ONSET", "0.5")))
    whisperx_vad_offset: float = field(default_factory=lambda: float(os.getenv("WHISPERX_VAD_OFFSET", "0.363")))
    stt_initial_prompt: str = field(default_factory=lambda: os.getenv(
        "STT_INITIAL_PROMPT", "A software system design meeting discussing services, APIs, databases, caches, queues, events, protocols, and cloud infrastructure."
    ))
    stt_hotwords: str = field(default_factory=lambda: os.getenv(
        "STT_HOTWORDS", "Kafka,Kubernetes,PostgreSQL,Redis,gRPC,GraphQL,WebSocket,microservice,API gateway"
    ))
    gemini_api_key: str | None = field(default_factory=lambda: os.getenv("GEMINI_API_KEY") or None)
    gemini_backend: str = field(default_factory=lambda: os.getenv("GEMINI_BACKEND", "developer").lower())
    google_cloud_location: str = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
    gemini_stt_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    segment_seconds: float = field(default_factory=lambda: float(os.getenv("SEGMENT_SECONDS", "8")))
    segment_overlap_seconds: float = field(default_factory=lambda: float(os.getenv("SEGMENT_OVERLAP_SECONDS", "1")))
    min_final_segment_seconds: float = field(default_factory=lambda: float(os.getenv("MIN_FINAL_SEGMENT_SECONDS", "0.5")))
    utterance_vad_enabled: bool = field(default_factory=lambda: _bool("UTTERANCE_VAD_ENABLED", True))
    vad_frame_ms: int = field(default_factory=lambda: int(os.getenv("VAD_FRAME_MS", "20")))
    vad_energy_threshold: int = field(default_factory=lambda: int(os.getenv("VAD_ENERGY_THRESHOLD", "350")))
    vad_onset_ms: int = field(default_factory=lambda: int(os.getenv("VAD_ONSET_MS", "100")))
    vad_offset_ms: int = field(default_factory=lambda: int(os.getenv("VAD_OFFSET_MS", "600")))
    vad_pre_speech_ms: int = field(default_factory=lambda: int(os.getenv("VAD_PRE_SPEECH_MS", "300")))
    vad_min_speech_ms: int = field(default_factory=lambda: int(os.getenv("VAD_MIN_SPEECH_MS", "250")))
    vad_max_utterance_seconds: float = field(default_factory=lambda: float(os.getenv("VAD_MAX_UTTERANCE_SECONDS", "25")))
    transcription_workers: int = field(default_factory=lambda: int(os.getenv("TRANSCRIPTION_WORKERS", "2")))
    transcription_queue_size: int = field(default_factory=lambda: int(os.getenv("TRANSCRIPTION_QUEUE_SIZE", "8")))
    intelligence_enabled: bool = field(default_factory=lambda: _bool("INTELLIGENCE_ENABLED", True))
    intelligence_provider: str = field(default_factory=lambda: os.getenv("INTELLIGENCE_PROVIDER", "gemini").lower())
    intelligence_model: str = field(default_factory=lambda: os.getenv("INTELLIGENCE_MODEL", "gemini-3.8-flash"))
    intelligence_api_key: str | None = field(default_factory=lambda: os.getenv("INTELLIGENCE_API_KEY") or os.getenv("GEMINI_API_KEY") or None)
    intelligence_base_url: str | None = field(default_factory=lambda: os.getenv("INTELLIGENCE_BASE_URL") or None)
    intelligence_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("INTELLIGENCE_TIMEOUT_SECONDS", "60")))
    intelligence_retry_attempts: int = field(default_factory=lambda: int(os.getenv("INTELLIGENCE_RETRY_ATTEMPTS", "3")))
    ollama_num_ctx: int = field(default_factory=lambda: int(os.getenv("OLLAMA_NUM_CTX", "16384")))
    ollama_think: bool = field(default_factory=lambda: _bool("OLLAMA_THINK", True))
    intelligence_batch_seconds: float = field(default_factory=lambda: float(os.getenv("INTELLIGENCE_WINDOW_SECONDS", "20")))
    intelligence_min_new_chars: int = field(default_factory=lambda: int(os.getenv("INTELLIGENCE_MIN_NEW_CHARS", "120")))
    intelligence_context_segments: int = field(default_factory=lambda: int(os.getenv("INTELLIGENCE_CONTEXT_SEGMENTS", "30")))
    evaluation_fixture_dir: Path = field(default_factory=lambda: Path(os.getenv(
        "EVAL_FIXTURE_DIR", str(BACKEND_ROOT / "eval" / "fixtures")
    )))
    enable_transcript_correction: bool = field(default_factory=lambda: _bool("ENABLE_TRANSCRIPT_CORRECTION", False))
    correction_provider: str = field(default_factory=lambda: os.getenv("CORRECTION_PROVIDER", os.getenv("INTELLIGENCE_PROVIDER", "qwen")).lower())
    correction_model: str = field(default_factory=lambda: os.getenv("CORRECTION_MODEL", os.getenv("INTELLIGENCE_MODEL", "qwen2.5:7b")))
    correction_confidence_threshold: float = field(default_factory=lambda: float(os.getenv("CORRECTION_CONFIDENCE_THRESHOLD", "0.78")))
    correction_queue_size: int = field(default_factory=lambda: int(os.getenv("CORRECTION_QUEUE_SIZE", "32")))
    enable_architecture_agent_v2: bool = field(default_factory=lambda: _bool("ENABLE_ARCHITECTURE_AGENT_V2", False))
    enable_diagram_mapper_v2: bool = field(default_factory=lambda: _bool("ENABLE_DIAGRAM_MAPPER_V2", False))
    architecture_default_backend_style: str = field(default_factory=lambda: os.getenv("DEFAULT_BACKEND_STYLE", "microservice").lower())
    architecture_default_sync_protocol: str = field(default_factory=lambda: os.getenv("DEFAULT_SYNC_PROTOCOL", "REST/HTTPS"))
    allow_generic_async_infrastructure: bool = field(default_factory=lambda: _bool("ALLOW_GENERIC_ASYNC_INFRASTRUCTURE", True))
    default_provenance_visibility: bool = field(default_factory=lambda: _bool("DEFAULT_PROVENANCE_VISIBILITY", True))
    prompt_logging: bool = field(default_factory=lambda: _bool("PROMPT_LOGGING", False))
    database_path: Path = field(default_factory=lambda: Path(os.getenv("MEETING_DATABASE_PATH", str(BACKEND_ROOT / "data" / "meetings.sqlite3"))))
    recordings_dir: Path = field(default_factory=lambda: Path(os.getenv("RECORDINGS_DIR", str(BACKEND_ROOT / "recordings"))))
    transcript_export_dir: Path = field(default_factory=lambda: Path(os.getenv("TRANSCRIPT_EXPORT_DIR", str(BACKEND_ROOT / "transcripts"))))
    firestore_enabled: bool = field(default_factory=lambda: _bool("FIRESTORE_ENABLED", False))
    firestore_required: bool = field(default_factory=lambda: _bool("FIRESTORE_REQUIRED", False))
    firestore_project_id: str | None = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or None)
    firestore_database: str = field(default_factory=lambda: os.getenv("FIRESTORE_DATABASE", "(default)"))
    firestore_collection: str = field(default_factory=lambda: os.getenv("FIRESTORE_MEETINGS_COLLECTION", "meetings"))

    def validate(self) -> None:
        if self.stt_provider not in {"whisper", "gemini"}:
            raise ValueError("STT_PROVIDER must be whisper or gemini")
        if self.intelligence_provider not in {"gemini", "grok", "groq", "qwen"}:
            raise ValueError("INTELLIGENCE_PROVIDER must be gemini, grok, groq, or qwen")
        if self.gemini_backend not in {"developer", "vertex"}:
            raise ValueError("GEMINI_BACKEND must be developer or vertex")
        if self.gemini_backend == "vertex" and not self.firestore_project_id:
            raise ValueError("GOOGLE_CLOUD_PROJECT is required for Vertex AI")
        if self.stt_provider == "gemini" and self.gemini_backend == "developer" and not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini STT")
        if self.intelligence_enabled and self.intelligence_provider in {"grok", "groq"} and not self.intelligence_api_key:
            raise ValueError("An intelligence API key is required for the selected provider")
        if self.intelligence_enabled and self.intelligence_provider == "gemini" and self.gemini_backend == "developer" and not self.intelligence_api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini intelligence with the developer backend")
        if self.correction_provider not in {"gemini", "grok", "groq", "qwen"}:
            raise ValueError("CORRECTION_PROVIDER must be gemini, grok, groq, or qwen")
        if not 0 <= self.correction_confidence_threshold <= 1:
            raise ValueError("CORRECTION_CONFIDENCE_THRESHOLD must be between 0 and 1")
        if self.architecture_default_backend_style not in {"backend_service", "microservice"}:
            raise ValueError("DEFAULT_BACKEND_STYLE must be backend_service or microservice")
        if self.enable_architecture_agent_v2 and not self.enable_diagram_mapper_v2:
            raise ValueError("ENABLE_ARCHITECTURE_AGENT_V2 requires ENABLE_DIAGRAM_MAPPER_V2")
        if not 0 <= self.segment_overlap_seconds < self.segment_seconds:
            raise ValueError("segment overlap must be smaller than segment length")
        if self.vad_frame_ms <= 0 or 1000 % self.vad_frame_ms:
            raise ValueError("VAD_FRAME_MS must be a positive divisor of 1000")
        if min(self.vad_energy_threshold, self.vad_onset_ms, self.vad_offset_ms,
               self.vad_pre_speech_ms, self.vad_min_speech_ms) < 0:
            raise ValueError("VAD settings cannot be negative")
        if self.vad_max_utterance_seconds <= 0:
            raise ValueError("VAD_MAX_UTTERANCE_SECONDS must be positive")
        if self.ollama_num_ctx < 4096:
            raise ValueError("OLLAMA_NUM_CTX must be at least 4096")
        if not 0 <= self.whisperx_vad_onset <= 1 or not 0 <= self.whisperx_vad_offset <= 1:
            raise ValueError("WhisperX VAD onset and offset must be between 0 and 1")
        if self.firestore_required and not self.firestore_enabled:
            raise ValueError("FIRESTORE_REQUIRED requires FIRESTORE_ENABLED=true")
