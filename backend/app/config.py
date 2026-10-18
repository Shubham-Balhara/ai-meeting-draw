"""Application settings (12-factor: read from env, sensible defaults).

Paths are derived relative to the ``backend/`` package root so the app runs the
same regardless of the process working directory.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

# backend/app/config.py -> backend/
BACKEND_ROOT = Path(__file__).resolve().parent.parent
# repo root (one above backend/)
REPO_ROOT = BACKEND_ROOT.parent

_DEFAULT_ORIGINS = (
    "http://127.0.0.1:8000,http://localhost:8000,"
    "http://127.0.0.1:3000,http://localhost:3000,"
    "http://127.0.0.1:5173,http://localhost:5173"
)


def _load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE .env loader (stdlib only; mirrors the companion apps).

    Uses setdefault so a real environment variable always wins over the file.
    Runs at import so os.environ is seeded before Settings/AIService read it.
    """
    if not path.exists():
        return
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv(REPO_ROOT / ".env")


def _default_origins() -> list[str]:
    return [
        o.strip()
        for o in os.environ.get("AI_MEETING_DRAW_ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",")
        if o.strip()
    ]


@dataclass
class Settings:
    """Runtime configuration."""

    storage_dir: Path = field(default_factory=lambda: BACKEND_ROOT / "storage")
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO").upper())
    allowed_origins: list[str] = field(default_factory=_default_origins)
    # Optional privacy-friendly analytics: set PLAUSIBLE_DOMAIN (e.g.
    # your deployment domain) and the served SPA shell gets the Plausible snippet.
    # PLAUSIBLE_SRC overrides the script host for self-hosted Plausible.
    plausible_domain: str | None = field(
        default_factory=lambda: os.environ.get("PLAUSIBLE_DOMAIN") or None
    )
    plausible_src: str = field(
        default_factory=lambda: os.environ.get("PLAUSIBLE_SRC")
        or "https://plausible.io/js/script.js"
    )
    # Optional Postgres persistence (postgresql://user:pass@host:5432/dbname).
    # Present + reachable → Postgres adapters; absent or unreachable → file
    # adapters with a boot warning, never a crash.
    database_url: str | None = field(
        default_factory=lambda: os.environ.get("DATABASE_URL") or None
    )
    # ---- S3-compatible object storage (Cloudflare R2 — DESIGN.md §7). All
    # optional: any missing ⇒ ObjectStorage.enabled is False and log-segment
    # shipping silently stays local-only (graceful degradation, as with AI).
    s3_endpoint_url: str | None = field(
        default_factory=lambda: os.environ.get("S3_ENDPOINT_URL") or None
    )
    s3_bucket: str | None = field(
        default_factory=lambda: os.environ.get("S3_BUCKET") or None
    )
    s3_access_key_id: str | None = field(
        default_factory=lambda: os.environ.get("S3_ACCESS_KEY_ID") or None
    )
    s3_secret_access_key: str | None = field(
        default_factory=lambda: os.environ.get("S3_SECRET_ACCESS_KEY") or None
    )
    s3_region: str = field(default_factory=lambda: os.environ.get("S3_REGION", "auto"))
    # Preferred SPA build output; falls back to the vanilla frontend when absent.
    web_dist: Path = field(default_factory=lambda: REPO_ROOT / "web" / "dist")
    frontend_dir: Path = field(default_factory=lambda: REPO_ROOT / "frontend")

    def configure_logging(self) -> None:
        """Apply the environment-controlled level to app and server logs."""
        level = getattr(logging, self.log_level, None)
        if not isinstance(level, int):
            raise ValueError(f"Invalid LOG_LEVEL: {self.log_level}")
        root = logging.getLogger()
        if not root.handlers:
            logging.basicConfig(
                level=level,
                format="%(asctime)s %(levelname)s %(name)s %(message)s",
            )
        root.setLevel(level)
        for handler in root.handlers:
            handler.setLevel(level)
        for name in ("ai_meeting_draw", "uvicorn", "uvicorn.error", "uvicorn.access"):
            logging.getLogger(name).setLevel(level)

    def frontend_path(self) -> Path | None:
        """The static dir to serve: built SPA if present, else vanilla frontend,
        else ``None`` (no frontend available)."""
        if self.web_dist.exists():
            return self.web_dist
        if self.frontend_dir.exists():
            return self.frontend_dir
        return None
