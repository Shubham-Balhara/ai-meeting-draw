import asyncio
import json
import logging
import sqlite3
from pathlib import Path

import pytest

from app.meeting.evaluation import (
    deterministic_reference_results,
    evaluate_rows,
    load_jsonl,
    validate_manifest,
)
from app.meeting.observability import event_log, safe_fields
from app.meeting.persistence_schema import MIGRATIONS
from app.meeting.storage import MeetingStore, MissingEntityError


FIXTURES = Path(__file__).resolve().parents[1] / "eval" / "fixtures"


def run(coro):
    return asyncio.run(coro)


def test_evaluation_corpus_is_versioned_unique_and_repeatable():
    manifest = validate_manifest(FIXTURES / "manifest.v1.json")
    corpus = load_jsonl(FIXTURES / manifest["corpus_file"])
    first = evaluate_rows(corpus, deterministic_reference_results(corpus))
    second = evaluate_rows(corpus, deterministic_reference_results(corpus))
    assert first == second
    assert first.cases == 18
    assert first.precision == first.recall == first.f1 == 1.0
    assert first.duplicate_count == first.unsupported_count == 0


def test_evaluator_counts_duplicates_unsupported_and_false_outputs():
    corpus = [{"case_id": "x", "turns": [{"sequence": 0}], "gold": {"concepts": [{"concept_id": "a"}]}}]
    result = [{"case_id": "x", "concepts": [{"concept_id": "a"}, {"concept_id": "a"}, {"concept_id": "b"}]}]
    metrics = evaluate_rows(corpus, result)
    assert metrics.true_positive == 1 and metrics.false_positive == 1
    assert metrics.duplicate_count == 1 and metrics.unsupported_count == 3


def test_observability_drops_content_and_secret_fields(caplog):
    sentinel = "secret-sentinel-value"
    cleaned = safe_fields({"meeting_id": "m", "api_key": sentinel, "raw_transcript": sentinel, "count": 2})
    assert cleaned == {"meeting_id": "m", "count": 2}
    logger = logging.getLogger("phase0-test")
    with caplog.at_level(logging.INFO):
        event_log(logger, logging.INFO, "meeting.started", meeting_id="m", api_key=sentinel, transcript=sentinel)
    assert sentinel not in caplog.text
    assert "meeting.started" in caplog.text


def test_v1_database_upgrades_to_v2_without_losing_rows(tmp_path):
    path = tmp_path / "upgrade.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    connection.executescript(MIGRATIONS[0][1])
    connection.execute("INSERT INTO schema_migrations VALUES (1,'fixed')")
    connection.execute(
        "INSERT INTO meetings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("m", "external", None, "active", "2026-01-01T00:00:00Z", None, "{}", None, None, "now", "now"),
    )
    connection.commit()
    connection.close()

    async def scenario():
        store = await MeetingStore(path).open()
        assert [row["version"] for row in await store.fetch_all("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2, 3, 4]
        assert (await store.meeting("m"))["external_id"] == "external"
        assert await store.fetch_all("SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_events'")
        await store.close()

    run(scenario())


def test_pipeline_run_lifecycle_events_validation_and_counters(tmp_path):
    async def scenario():
        store = await MeetingStore(tmp_path / "events.sqlite3").open()
        await store.create_meeting("external", meeting_id="m")
        await store.create_recording("m", "recording.webm", "whisperx", recording_id="r")
        await store.start_pipeline_run(
            meeting_id="m", recording_id="r", run_id="run", correlation_id="corr",
            stt_provider="whisperx", stt_model="small", intelligence_provider="qwen", intelligence_model="qwen2.5",
        )
        event_id = await store.observe_pipeline_event(
            run_id="run", meeting_id="m", recording_id="r", event_name="stt.request.succeeded",
            stage="stt", status="succeeded", sequence=0, duration_ms=12, output_count=1,
            attributes={"language": "en"},
        )
        assert event_id > 0
        await store.increment_run_counters("run", segments_seen=1, transcripts_stored=1)
        await store.finish_pipeline_run("run", "completed")
        summary = await store.pipeline_run_summary("run")
        assert summary["run"]["status"] == "completed"
        assert summary["run"]["segments_seen"] == summary["run"]["transcripts_stored"] == 1
        assert [event["event_name"] for event in summary["events"]] == ["stt.request.succeeded"]
        with pytest.raises(ValueError, match="invalid pipeline stage"):
            await store.observe_pipeline_event(run_id="run", meeting_id="m", event_name="bad", stage="bad", status="succeeded")
        with pytest.raises(ValueError, match="sensitive"):
            await store.observe_pipeline_event(
                run_id="run", meeting_id="m", event_name="bad", stage="stt", status="failed",
                attributes={"authorization_token": "sentinel"},
            )
        with pytest.raises(MissingEntityError):
            await store.finish_pipeline_run("missing", "failed")
        await store.close()

    run(scenario())


def test_pipeline_events_are_serialized_without_loss(tmp_path):
    async def scenario():
        store = await MeetingStore(tmp_path / "concurrent.sqlite3").open()
        await store.create_meeting("external", meeting_id="m")
        await store.start_pipeline_run(meeting_id="m", run_id="run", correlation_id="corr")
        await asyncio.gather(*(
            store.observe_pipeline_event(
                run_id="run", meeting_id="m", event_name="stt.request.succeeded",
                stage="stt", status="succeeded", sequence=index,
            ) for index in range(100)
        ))
        summary = await store.pipeline_run_summary("run")
        assert len(summary["events"]) == 100
        ids = [row["id"] for row in summary["events"]]
        assert ids == sorted(ids) and len(ids) == len(set(ids))
        await store.close()

    run(scenario())
