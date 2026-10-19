from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from .persistence_schema import MIGRATIONS
from .firestore_mirror import FirestoreMirror

T = TypeVar("T")
logger = logging.getLogger("ai_meeting_draw.meeting.storage")

PIPELINE_STAGES = frozenset({"meeting", "recording", "decode", "stt", "storage", "intelligence", "diagram", "export", "websocket"})
PIPELINE_EVENT_STATUSES = frozenset({"started", "succeeded", "failed", "retried", "skipped", "cancelled"})
PIPELINE_RUN_STATUSES = frozenset({"running", "completed", "disconnected", "failed"})
MAX_EVENT_ATTRIBUTES_BYTES = 8192
FORBIDDEN_EVENT_ATTRIBUTE_PARTS = ("key", "token", "authorization", "prompt", "transcript", "audio", "response_body")


class MissingEntityError(LookupError):
    """Raised when a lifecycle update targets an entity that does not exist."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, separators=(",", ":"), sort_keys=True)


class MeetingStore:
    """A single-connection async facade; all SQLite access is serialized off-loop."""

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000, synchronous: str = "NORMAL",
                 firestore: FirestoreMirror | None = None):
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.synchronous = synchronous.upper()
        if self.synchronous not in {"OFF", "NORMAL", "FULL", "EXTRA"}:
            raise ValueError("synchronous must be OFF, NORMAL, FULL, or EXTRA")
        self._connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self.firestore = firestore or FirestoreMirror(enabled=False)
        self._recording_meetings: dict[str, str] = {}

    async def open(self) -> "MeetingStore":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            if self._connection is None:
                self._connection = await asyncio.to_thread(self._connect)
                await asyncio.to_thread(self._migrate, self._connection)
                logger.info("Meeting database opened path=%s", self.path)
        await self.firestore.open()
        return self

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        connection.execute(f"PRAGMA synchronous={self.synchronous}")
        return connection

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            try:
                connection.executescript("BEGIN IMMEDIATE;\n" + sql + f"\nINSERT INTO schema_migrations VALUES ({version}, '{utc_now()}');\nCOMMIT;")
            except BaseException:
                connection.rollback()
                raise

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                connection, self._connection = self._connection, None
                await asyncio.to_thread(connection.close)
                logger.info("Meeting database closed path=%s", self.path)
        await self.firestore.close()

    async def _run(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        async with self._lock:
            if self._connection is None:
                raise RuntimeError("MeetingStore is not open")
            return await asyncio.to_thread(operation, self._connection)

    async def _write(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        def operation(connection: sqlite3.Connection) -> int:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(sql, params)
                connection.commit()
                return cursor.rowcount
            except BaseException:
                connection.rollback()
                raise
        rows = await self._run(operation)
        logger.debug("SQLite write completed rows=%d operation=%s", rows, sql.split(None, 1)[0])
        return rows

    async def create_meeting(self, external_id: str, *, meeting_id: str | None = None,
                             title: str | None = None, client_metadata: dict | None = None,
                             intelligence_provider: str | None = None,
                             intelligence_model: str | None = None,
                             started_at: str | None = None) -> str:
        meeting_id, now = meeting_id or str(uuid.uuid4()), started_at or utc_now()
        await self._write(
            "INSERT INTO meetings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (meeting_id, external_id, title, "active", now, None, _json(client_metadata),
             intelligence_provider, intelligence_model, now, now),
        )
        logger.info("Meeting created meeting_id=%s external_id=%s", meeting_id, external_id)
        await self.firestore.meeting_created(meeting_id, {
            "id": meeting_id, "external_id": external_id, "title": title,
            "status": "active", "started_at": now, "updated_at": now,
            "client_metadata": client_metadata or {},
            "intelligence_provider": intelligence_provider,
            "intelligence_model": intelligence_model,
        })
        return meeting_id

    async def start_pipeline_run(
        self,
        *,
        meeting_id: str,
        correlation_id: str,
        recording_id: str | None = None,
        run_id: str | None = None,
        stt_provider: str | None = None,
        stt_model: str | None = None,
        intelligence_provider: str | None = None,
        intelligence_model: str | None = None,
        started_at: str | None = None,
    ) -> str:
        run_id, now = run_id or str(uuid.uuid4()), started_at or utc_now()
        await self._write(
            """INSERT INTO pipeline_runs(
               id,meeting_id,recording_id,correlation_id,started_at,status,
               stt_provider,stt_model,intelligence_provider,intelligence_model,created_at,updated_at)
               VALUES (?,?,?,?,?,'running',?,?,?,?,?,?)""",
            (run_id, meeting_id, recording_id, correlation_id, now, stt_provider, stt_model,
             intelligence_provider, intelligence_model, now, now),
        )
        logger.info("Pipeline run started run_id=%s meeting_id=%s", run_id, meeting_id)
        return run_id

    async def finish_pipeline_run(self, run_id: str, status: str, *, ended_at: str | None = None) -> None:
        if status not in PIPELINE_RUN_STATUSES - {"running"}:
            raise ValueError(f"invalid terminal pipeline run status: {status}")
        rows = await self._write(
            "UPDATE pipeline_runs SET status=?,ended_at=?,updated_at=? WHERE id=?",
            (status, ended_at or utc_now(), ended_at or utc_now(), run_id),
        )
        if rows != 1:
            raise MissingEntityError(f"pipeline run does not exist: {run_id}")
        logger.info("Pipeline run finalized run_id=%s status=%s", run_id, status)

    @staticmethod
    def _event_attributes(attributes: Mapping[str, Any] | None) -> str:
        value = dict(attributes or {})
        for key in value:
            lowered = key.lower()
            if any(part in lowered for part in FORBIDDEN_EVENT_ATTRIBUTE_PARTS):
                raise ValueError(f"sensitive pipeline event attribute is forbidden: {key}")
        encoded = _json(value)
        if len(encoded.encode("utf-8")) > MAX_EVENT_ATTRIBUTES_BYTES:
            raise ValueError("pipeline event attributes exceed 8192 bytes")
        return encoded

    async def observe_pipeline_event(
        self,
        *,
        run_id: str,
        meeting_id: str,
        event_name: str,
        stage: str,
        status: str,
        recording_id: str | None = None,
        sequence: int | None = None,
        attempt: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        duration_ms: int | None = None,
        queue_depth: int | None = None,
        input_count: int | None = None,
        output_count: int | None = None,
        error_code: str | None = None,
        attributes: Mapping[str, Any] | None = None,
        occurred_at: str | None = None,
    ) -> int:
        if stage not in PIPELINE_STAGES:
            raise ValueError(f"invalid pipeline stage: {stage}")
        if status not in PIPELINE_EVENT_STATUSES:
            raise ValueError(f"invalid pipeline event status: {status}")
        if not event_name or len(event_name) > 160:
            raise ValueError("event_name must contain 1 to 160 characters")
        if duration_ms is not None and duration_ms < 0:
            raise ValueError("duration_ms cannot be negative")
        if queue_depth is not None and queue_depth < 0:
            raise ValueError("queue_depth cannot be negative")
        encoded_attributes = self._event_attributes(attributes)

        def operation(connection: sqlite3.Connection) -> int:
            cursor = connection.execute(
                """INSERT INTO pipeline_events(
                   run_id,meeting_id,recording_id,event_name,stage,status,sequence,attempt,provider,model,
                   duration_ms,queue_depth,input_count,output_count,error_code,attributes_json,occurred_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, meeting_id, recording_id, event_name, stage, status, sequence, attempt,
                 provider, model, duration_ms, queue_depth, input_count, output_count, error_code,
                 encoded_attributes, occurred_at or utc_now()),
            )
            return int(cursor.lastrowid)

        event_id = await self._run(operation)
        logger.debug("Pipeline event stored event_id=%d run_id=%s event=%s status=%s", event_id, run_id, event_name, status)
        return event_id

    async def increment_run_counters(self, run_id: str, **deltas: int) -> None:
        allowed = {"segments_seen", "transcripts_stored", "insights_stored", "diagram_ops_applied", "error_count"}
        if not deltas or set(deltas) - allowed:
            raise ValueError(f"invalid pipeline counter(s): {sorted(set(deltas) - allowed)}")
        if any(not isinstance(value, int) or value < 0 for value in deltas.values()):
            raise ValueError("pipeline counter deltas must be non-negative integers")
        assignments = ",".join(f"{name}={name}+?" for name in sorted(deltas))
        values = tuple(deltas[name] for name in sorted(deltas))
        rows = await self._write(
            f"UPDATE pipeline_runs SET {assignments},updated_at=? WHERE id=?",
            values + (utc_now(), run_id),
        )
        if rows != 1:
            raise MissingEntityError(f"pipeline run does not exist: {run_id}")

    async def pipeline_run_summary(self, run_id: str) -> dict[str, Any] | None:
        runs = await self.fetch_all("SELECT * FROM pipeline_runs WHERE id=?", (run_id,))
        if not runs:
            return None
        events = await self.fetch_all("SELECT * FROM pipeline_events WHERE run_id=? ORDER BY id", (run_id,))
        return {"run": runs[0], "events": events}

    async def finish_meeting(self, meeting_id: str, status: str = "completed", *, ended_at: str | None = None) -> None:
        now = ended_at or utc_now()
        await self._write("UPDATE meetings SET status=?, ended_at=?, updated_at=? WHERE id=?", (status, now, now, meeting_id))
        await self.firestore.meeting_finished(meeting_id, {"status": status, "ended_at": now, "updated_at": now})
        logger.info("Meeting finalized meeting_id=%s status=%s", meeting_id, status)

    async def create_recording(self, meeting_id: str, file_path: str, stt_provider: str, *,
                               recording_id: str | None = None, stt_model: str | None = None,
                               mime_type: str = "audio/webm", started_at: str | None = None) -> str:
        recording_id, now = recording_id or str(uuid.uuid4()), started_at or utc_now()
        await self._write(
            "INSERT INTO recordings(id,meeting_id,file_path,mime_type,stt_provider,stt_model,status,started_at) VALUES (?,?,?,?,?,?,?,?)",
            (recording_id, meeting_id, file_path, mime_type, stt_provider, stt_model, "active", now),
        )
        logger.info("Recording created recording_id=%s meeting_id=%s", recording_id, meeting_id)
        self._recording_meetings[recording_id] = meeting_id
        await self.firestore.recording_upserted(meeting_id, recording_id, {
            "id": recording_id, "meeting_id": meeting_id, "mime_type": mime_type,
            "stt_provider": stt_provider, "stt_model": stt_model,
            "status": "active", "started_at": now,
        })
        return recording_id

    async def update_recording_progress(self, recording_id: str, *, bytes_received: int, chunks_received: int) -> None:
        await self._write("UPDATE recordings SET bytes_received=?, chunks_received=? WHERE id=?",
                          (bytes_received, chunks_received, recording_id))
        meeting_id = self._recording_meetings.get(recording_id)
        if meeting_id:
            await self.firestore.recording_upserted(meeting_id, recording_id, {
                "bytes_received": bytes_received, "chunks_received": chunks_received,
            })

    async def finish_recording(self, recording_id: str, *, status: str = "completed", duration_ms: int | None = None,
                               bytes_received: int | None = None, chunks_received: int | None = None,
                               sha256: str | None = None, ended_at: str | None = None) -> None:
        await self._write(
            """UPDATE recordings SET status=?, ended_at=?, duration_ms=COALESCE(?,duration_ms),
               bytes_received=COALESCE(?,bytes_received), chunks_received=COALESCE(?,chunks_received),
               sha256=COALESCE(?,sha256) WHERE id=?""",
            (status, ended_at or utc_now(), duration_ms, bytes_received, chunks_received, sha256, recording_id),
        )
        logger.info("Recording finalized recording_id=%s status=%s", recording_id, status)
        meeting_id = self._recording_meetings.get(recording_id)
        if meeting_id:
            await self.firestore.recording_upserted(meeting_id, recording_id, {
                "status": status, "ended_at": ended_at or utc_now(), "duration_ms": duration_ms,
                "bytes_received": bytes_received, "chunks_received": chunks_received, "sha256": sha256,
            })

    async def add_transcript(self, *, meeting_id: str, recording_id: str, sequence: int,
                             start_ms: int, end_ms: int, text: str, provider: str,
                             captured_at: str | None = None, normalized_text: str | None = None,
                             model: str | None = None, language: str | None = None,
                             confidence: float | None = None, is_final: bool = True,
                             raw_response: Any = None) -> bool:
        now = utc_now()
        values = (meeting_id, recording_id, sequence, start_ms, end_ms, captured_at, text,
                  normalized_text, provider, model, language, confidence, int(is_final),
                  None if raw_response is None else _json(raw_response), now)

        def operation(connection: sqlite3.Connection) -> bool:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT start_ms,end_ms,text,provider FROM transcript_segments WHERE recording_id=? AND sequence=?",
                    (recording_id, sequence),
                ).fetchone()
                if existing:
                    expected = (start_ms, end_ms, text, provider)
                    if tuple(existing) != expected:
                        raise ValueError(f"conflicting transcript for recording={recording_id} sequence={sequence}")
                    connection.commit()
                    return False
                connection.execute("""INSERT INTO transcript_segments(
                    meeting_id,recording_id,sequence,start_ms,end_ms,captured_at,text,normalized_text,
                    provider,model,language,confidence,is_final,raw_response_json,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise
        inserted = await self._run(operation)
        if inserted:
            await self.firestore.transcript_added(meeting_id, recording_id, sequence, {
                "meeting_id": meeting_id, "recording_id": recording_id, "sequence": sequence,
                "start_ms": start_ms, "end_ms": end_ms, "captured_at": captured_at,
                "text": text, "normalized_text": normalized_text, "provider": provider,
                "model": model, "language": language, "confidence": confidence,
                "is_final": is_final, "created_at": now,
            })
        logger.debug("Transcript stored recording_id=%s sequence=%d inserted=%s", recording_id, sequence, inserted)
        return inserted

    async def add_error(self, *, stage: str, message: str, meeting_id: str | None = None,
                        recording_id: str | None = None, sequence: int | None = None,
                        provider: str | None = None, code: str | None = None,
                        details: Any = None, retryable: bool = False, attempt: int | None = None) -> int:
        def operation(connection: sqlite3.Connection) -> int:
            cursor = connection.execute("""INSERT INTO processing_errors(
                meeting_id,recording_id,stage,sequence,provider,code,message,details_json,retryable,attempt,occurred_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (meeting_id, recording_id, stage, sequence, provider, code, message, _json(details),
                 int(retryable), attempt, utc_now()))
            return int(cursor.lastrowid)
        error_id = await self._run(operation)
        logger.error("Processing error stored id=%d stage=%s meeting_id=%s message=%s", error_id, stage, meeting_id, message)
        return error_id

    async def upsert_insight(self, *, insight_id: str, meeting_id: str, dedupe_key: str,
                             kind: str, status: str, text: str, owner: str | None = None,
                             due_at: str | None = None, confidence: float | None = None,
                             style: Any = None, source_start_sequence: int | None = None,
                             source_end_sequence: int | None = None, provider: str | None = None,
                             model: str | None = None, supersedes_id: str | None = None) -> tuple[str, int]:
        def operation(connection: sqlite3.Connection) -> tuple[str, int]:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT id,revision FROM intelligence_insights WHERE meeting_id=? AND dedupe_key=?",
                    (meeting_id, dedupe_key),
                ).fetchone()
                target_id, revision = (row[0], row[1] + 1) if row else (insight_id, 1)
                now = utc_now()
                payload = dict(kind=kind, status=status, text=text, owner=owner, due_at=due_at,
                               confidence=confidence, style=style or {}, source_start_sequence=source_start_sequence,
                               source_end_sequence=source_end_sequence, provider=provider, model=model,
                               supersedes_id=supersedes_id)
                if row:
                    connection.execute("""UPDATE intelligence_insights SET kind=?,status=?,text=?,owner=?,due_at=?,
                        confidence=?,style_json=?,source_start_sequence=?,source_end_sequence=?,provider=?,model=?,
                        revision=?,supersedes_id=?,updated_at=? WHERE id=?""",
                        (kind,status,text,owner,due_at,confidence,_json(style),source_start_sequence,
                         source_end_sequence,provider,model,revision,supersedes_id,now,target_id))
                else:
                    connection.execute("""INSERT INTO intelligence_insights VALUES
                        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (target_id,meeting_id,kind,status,text,owner,due_at,confidence,_json(style),
                         source_start_sequence,source_end_sequence,provider,model,dedupe_key,revision,
                         supersedes_id,now,now))
                connection.execute("INSERT INTO insight_revisions VALUES (?,?,?,?)",
                                   (target_id, revision, _json(payload), now))
                connection.commit()
                return target_id, revision
            except BaseException:
                connection.rollback()
                raise
        return await self._run(operation)

    async def enqueue_diagram_operation(self, *, operation_id: str, meeting_id: str, ordinal: int,
                                        operation_type: str, request: Any, insight_id: str | None = None,
                                        target_id: str | None = None) -> bool:
        return bool(await self._write("""INSERT OR IGNORE INTO diagram_operations(
            id,meeting_id,insight_id,ordinal,operation_type,target_id,request_json,status,created_at)
            VALUES (?,?,?,?,?,?,?,'pending',?)""",
            (operation_id, meeting_id, insight_id, ordinal, operation_type, target_id, _json(request), utc_now())))

    async def complete_diagram_operation(self, operation_id: str, *, response: Any = None,
                                         status: str = "applied", error_message: str | None = None) -> None:
        await self._write("""UPDATE diagram_operations SET response_json=?,status=?,attempt_count=attempt_count+1,
            error_message=?,applied_at=CASE WHEN ?='applied' THEN ? ELSE applied_at END WHERE id=?""",
            (None if response is None else _json(response), status, error_message, status, utc_now(), operation_id))

    async def bind_diagram_element(self, *, meeting_id: str, insight_id: str, element_id: str,
                                   element_type: str | None = None, operation_id: str | None = None) -> None:
        now = utc_now()
        await self._write("""INSERT INTO diagram_bindings VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(meeting_id,insight_id) DO UPDATE SET element_id=excluded.element_id,
            element_type=excluded.element_type,last_operation_id=excluded.last_operation_id,updated_at=excluded.updated_at""",
            (meeting_id, insight_id, element_id, element_type, operation_id, now, now))

    async def set_checkpoint(self, meeting_id: str, processor: str, last_sequence: int) -> None:
        await self._write("""INSERT INTO processor_checkpoints VALUES (?,?,?,?)
            ON CONFLICT(meeting_id,processor) DO UPDATE SET last_sequence=excluded.last_sequence,
            updated_at=excluded.updated_at""", (meeting_id, processor, last_sequence, utc_now()))

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return await self._run(lambda connection: [dict(row) for row in connection.execute(sql, params).fetchall()])

    async def transcript_segments(self, meeting_id: str) -> list[dict[str, Any]]:
        return await self.fetch_all("""SELECT t.*, r.started_at AS recording_started_at
            FROM transcript_segments t JOIN recordings r ON r.id=t.recording_id
            WHERE t.meeting_id=? ORDER BY r.started_at,t.start_ms,t.sequence""", (meeting_id,))

    async def transcript_by_sequence(self, recording_id: str, sequence: int) -> dict[str, Any] | None:
        rows = await self.fetch_all(
            "SELECT * FROM transcript_segments WHERE recording_id=? AND sequence=?",
            (recording_id, sequence),
        )
        return rows[0] if rows else None

    async def add_transcript_correction(
        self, *, correction_id: str, transcript_id: int, meeting_id: str, raw_text: str,
        corrected_text: str, changed: bool, edits: Any, unresolved: Any,
        confidence: float | None, provider: str, model: str, prompt_version: str,
    ) -> int:
        def operation(connection: sqlite3.Connection) -> int:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT COALESCE(MAX(revision),0)+1 FROM transcript_corrections WHERE transcript_id=?",
                    (transcript_id,),
                ).fetchone()
                revision = int(row[0])
                connection.execute(
                    """INSERT INTO transcript_corrections VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (correction_id, transcript_id, meeting_id, revision, raw_text, corrected_text,
                     int(changed), _json(edits), _json(unresolved), confidence, provider, model,
                     prompt_version, utc_now()),
                )
                connection.commit()
                return revision
            except BaseException:
                connection.rollback()
                raise
        revision = await self._run(operation)
        await self.firestore.correction_added(meeting_id, correction_id, {
            "id": correction_id, "transcript_id": transcript_id, "revision": revision,
            "raw_text": raw_text, "corrected_text": corrected_text, "changed": changed,
            "edits": edits, "unresolved": unresolved, "confidence": confidence,
            "provider": provider, "model": model, "prompt_version": prompt_version,
            "created_at": utc_now(),
        })
        return revision

    async def latest_corrections(self, meeting_id: str) -> list[dict[str, Any]]:
        return await self.fetch_all(
            """SELECT c.* FROM transcript_corrections c
               JOIN (SELECT transcript_id,MAX(revision) revision FROM transcript_corrections
                     WHERE meeting_id=? GROUP BY transcript_id) latest
                 ON latest.transcript_id=c.transcript_id AND latest.revision=c.revision
               ORDER BY c.transcript_id""",
            (meeting_id,),
        )

    async def architecture_graph(self, meeting_id: str) -> dict[str, Any]:
        rows = await self.fetch_all("SELECT version,graph_json FROM architecture_graphs WHERE meeting_id=?", (meeting_id,))
        if not rows:
            return {"version": 0, "entities": {}, "relationships": {}, "questions": {}}
        graph = json.loads(rows[0]["graph_json"])
        graph["version"] = rows[0]["version"]
        return graph

    async def commit_architecture_mutation(
        self, *, meeting_id: str, mutation_id: str, operation: str, target_id: str,
        mutation_payload: Any, resulting_graph: Any, provenance: str, confidence: float,
        evidence: Any, base_version: int, agent_run_id: str | None = None,
    ) -> int:
        """Compare-and-swap one mutation and graph snapshot in one transaction."""
        graph_payload = resulting_graph.model_dump(mode="json") if hasattr(resulting_graph, "model_dump") else resulting_graph
        if graph_payload.get("version") != base_version + 1:
            raise ValueError("resulting graph version must be base_version + 1")
        def operation_fn(connection: sqlite3.Connection) -> int:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT version FROM architecture_graphs WHERE meeting_id=?", (meeting_id,)).fetchone()
                current = int(row[0]) if row else 0
                if current != base_version:
                    raise ValueError(f"stale architecture graph version: expected {base_version}, current {current}")
                ordinal_row = connection.execute("SELECT COALESCE(MAX(ordinal),-1)+1 FROM architecture_mutations WHERE meeting_id=?", (meeting_id,)).fetchone()
                ordinal, now = int(ordinal_row[0]), utc_now()
                graph_json = _json({k:v for k,v in graph_payload.items() if k != "version"})
                connection.execute("""INSERT INTO architecture_graphs(meeting_id,version,graph_json,updated_at) VALUES (?,?,?,?)
                    ON CONFLICT(meeting_id) DO UPDATE SET version=excluded.version,graph_json=excluded.graph_json,updated_at=excluded.updated_at""",
                    (meeting_id, base_version+1, graph_json, now))
                connection.execute("""INSERT INTO architecture_mutations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (mutation_id, meeting_id, ordinal, base_version, base_version+1, operation, target_id,
                     _json(mutation_payload), provenance, confidence, _json(evidence), agent_run_id, now))
                connection.commit()
                return base_version + 1
            except BaseException:
                connection.rollback(); raise
        version = await self._run(operation_fn)
        await self.firestore.architecture_updated(meeting_id, {
            "meeting_id": meeting_id, "version": version, "graph": graph_payload,
            "last_mutation_id": mutation_id, "updated_at": utc_now(),
        })
        return version

    async def meeting(self, meeting_id: str) -> dict[str, Any] | None:
        rows = await self.fetch_all("SELECT * FROM meetings WHERE id=?", (meeting_id,))
        return rows[0] if rows else None

    async def record_artifact(self, *, artifact_id: str, meeting_id: str, kind: str, path: str, sha256: str) -> None:
        await self._write("""INSERT INTO artifacts VALUES (?,?,?,?,?,?)
            ON CONFLICT(meeting_id,kind) DO UPDATE SET id=excluded.id,path=excluded.path,
            sha256=excluded.sha256,created_at=excluded.created_at""",
            (artifact_id, meeting_id, kind, path, sha256, utc_now()))
