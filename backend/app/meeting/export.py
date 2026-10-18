from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from .storage import MeetingStore

logger = logging.getLogger("ai_meeting_draw.meeting.export")


def _clock(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _atomic_write(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return hashlib.sha256(content).hexdigest()
    finally:
        temporary.unlink(missing_ok=True)


async def export_transcript(store: MeetingStore, meeting_id: str, directory: str | Path) -> dict[str, Path]:
    logger.info("Exporting transcript meeting_id=%s", meeting_id)
    meeting, segments = await store.meeting(meeting_id), await store.transcript_segments(meeting_id)
    corrections = await store.latest_corrections(meeting_id)
    correction_by_transcript = {row["transcript_id"]: row for row in corrections}
    if meeting is None:
        raise KeyError(f"meeting not found: {meeting_id}")
    directory = Path(directory)
    text_lines = [
        f"Meeting: {meeting['external_id']}",
        f"Started (UTC): {meeting['started_at']}",
        f"Ended (UTC): {meeting['ended_at'] or ''}",
        "",
    ]
    for segment in segments:
        correction = correction_by_transcript.get(segment["id"])
        displayed = correction["corrected_text"] if correction else segment["text"]
        text_lines.append(f"[{_clock(segment['start_ms'])} - {_clock(segment['end_ms'])}] {displayed}")
        if correction and correction["changed"]:
            text_lines.append(f"  Raw STT: {segment['text']}")
    text_content = ("\n".join(text_lines) + "\n").encode("utf-8")
    json_content = json.dumps(
        {"schema_version": 1, "meeting": meeting, "segments": segments, "corrections": corrections},
        ensure_ascii=False, indent=2, sort_keys=True,
    ).encode("utf-8") + b"\n"
    text_path, json_path = directory / f"meeting-{meeting_id}.txt", directory / f"meeting-{meeting_id}.json"
    text_hash, json_hash = await asyncio.gather(
        asyncio.to_thread(_atomic_write, text_path, text_content),
        asyncio.to_thread(_atomic_write, json_path, json_content),
    )
    await store.record_artifact(artifact_id=str(uuid.uuid4()), meeting_id=meeting_id,
                                kind="transcript_txt", path=str(text_path), sha256=text_hash)
    await store.record_artifact(artifact_id=str(uuid.uuid4()), meeting_id=meeting_id,
                                kind="transcript_json", path=str(json_path), sha256=json_hash)
    logger.info("Transcript exported meeting_id=%s segments=%d directory=%s", meeting_id, len(segments), directory)
    return {"text": text_path, "json": json_path}
