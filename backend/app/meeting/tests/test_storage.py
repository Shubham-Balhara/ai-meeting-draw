import asyncio
import json

import pytest

from app.meeting.export import export_transcript
from app.meeting.storage import MeetingStore


def run(coroutine):
    return asyncio.run(coroutine)


def test_lifecycle_idempotency_and_export(tmp_path):
    async def scenario():
        store = await MeetingStore(tmp_path / "meetings.sqlite3").open()
        meeting = await store.create_meeting("same-human-meeting", meeting_id="m1", started_at="2026-01-01T00:00:00+00:00")
        recording = await store.create_recording(meeting, "recording.webm", "whisper", recording_id="r1")
        assert await store.add_transcript(meeting_id=meeting, recording_id=recording, sequence=1,
            start_ms=7000, end_ms=15000, text="second", provider="whisper")
        assert await store.add_transcript(meeting_id=meeting, recording_id=recording, sequence=0,
            start_ms=0, end_ms=8000, text="first", provider="whisper")
        assert not await store.add_transcript(meeting_id=meeting, recording_id=recording, sequence=0,
            start_ms=0, end_ms=8000, text="first", provider="whisper")
        with pytest.raises(ValueError):
            await store.add_transcript(meeting_id=meeting, recording_id=recording, sequence=0,
                start_ms=0, end_ms=8000, text="conflict", provider="whisper")
        await store.finish_recording(recording, duration_ms=15000, bytes_received=12, chunks_received=2)
        await store.finish_meeting(meeting)
        paths = await export_transcript(store, meeting, tmp_path / "exports")
        assert paths["text"].read_text().index("first") < paths["text"].read_text().index("second")
        assert json.loads(paths["json"].read_text())["schema_version"] == 1
        assert len(await store.fetch_all("SELECT * FROM artifacts")) == 2
        await store.close()

        reopened = await MeetingStore(tmp_path / "meetings.sqlite3").open()
        assert len(await reopened.transcript_segments("m1")) == 2
        migrations = await reopened.fetch_all("SELECT version FROM schema_migrations ORDER BY version")
        assert [row["version"] for row in migrations] == [1, 2, 3, 4]
        await reopened.close()
    run(scenario())


def test_insight_revisions_and_diagram_outbox(tmp_path):
    async def scenario():
        store = await MeetingStore(tmp_path / "db.sqlite3").open()
        await store.create_meeting("external", meeting_id="m")
        insight_id, revision = await store.upsert_insight(
            insight_id="i", meeting_id="m", dedupe_key="decision:architecture", kind="suggestion",
            status="proposed", text="Use queues", style={"stroke": "dashed"})
        assert (insight_id, revision) == ("i", 1)
        insight_id, revision = await store.upsert_insight(
            insight_id="ignored", meeting_id="m", dedupe_key="decision:architecture", kind="decision",
            status="confirmed", text="Use queues", style={"stroke": "solid"})
        assert (insight_id, revision) == ("i", 2)
        assert len(await store.fetch_all("SELECT * FROM insight_revisions")) == 2

        assert await store.enqueue_diagram_operation(operation_id="op", meeting_id="m", ordinal=0,
            operation_type="create", request={"label": "Use queues"}, insight_id="i")
        assert not await store.enqueue_diagram_operation(operation_id="op", meeting_id="m", ordinal=0,
            operation_type="create", request={"label": "Use queues"}, insight_id="i")
        pending = await store.fetch_all("SELECT * FROM diagram_operations WHERE status='pending'")
        assert len(pending) == 1
        await store.complete_diagram_operation("op", response={"element_id": "e"})
        await store.bind_diagram_element(meeting_id="m", insight_id="i", element_id="e", operation_id="op")
        assert (await store.fetch_all("SELECT status FROM diagram_operations"))[0]["status"] == "applied"
        assert (await store.fetch_all("SELECT element_id FROM diagram_bindings"))[0]["element_id"] == "e"
        await store.close()
    run(scenario())


def test_errors_progress_and_checkpoints(tmp_path):
    async def scenario():
        store = await MeetingStore(tmp_path / "db.sqlite3").open()
        await store.create_meeting("external", meeting_id="m")
        await store.create_recording("m", "x.webm", "gemini", recording_id="r")
        await store.update_recording_progress("r", bytes_received=99, chunks_received=3)
        error_id = await store.add_error(meeting_id="m", recording_id="r", stage="stt",
                                         sequence=2, message="temporary", retryable=True, attempt=1)
        await store.set_checkpoint("m", "intelligence", 7)
        assert error_id > 0
        assert (await store.fetch_all("SELECT bytes_received FROM recordings"))[0]["bytes_received"] == 99
        assert (await store.fetch_all("SELECT last_sequence FROM processor_checkpoints"))[0]["last_sequence"] == 7
        await store.close()
    run(scenario())
