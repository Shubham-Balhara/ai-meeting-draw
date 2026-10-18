"""SQLite migrations kept separate from the public intelligence contracts.

Never edit a released migration.  Existing meeting databases are upgraded by
appending a new migration so a clean database and an upgraded database follow
the same history.
"""

MIGRATIONS = ((1, r"""
CREATE TABLE meetings (
 id TEXT PRIMARY KEY, external_id TEXT NOT NULL, title TEXT,
 status TEXT NOT NULL CHECK(status IN ('starting','active','finalizing','completed','completed_with_errors','disconnected','failed')),
 started_at TEXT NOT NULL, ended_at TEXT, client_metadata_json TEXT NOT NULL DEFAULT '{}',
 intelligence_provider TEXT, intelligence_model TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_meetings_external_started ON meetings(external_id,started_at);
CREATE TABLE recordings (
 id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 file_path TEXT NOT NULL, mime_type TEXT NOT NULL DEFAULT 'audio/webm', stt_provider TEXT NOT NULL,
 stt_model TEXT, status TEXT NOT NULL CHECK(status IN ('starting','active','completed','disconnected','failed')),
 started_at TEXT NOT NULL, ended_at TEXT, duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms>=0),
 bytes_received INTEGER NOT NULL DEFAULT 0 CHECK(bytes_received>=0),
 chunks_received INTEGER NOT NULL DEFAULT 0 CHECK(chunks_received>=0), sha256 TEXT
);
CREATE INDEX idx_recordings_meeting ON recordings(meeting_id,started_at);
CREATE TABLE transcript_segments (
 id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
 sequence INTEGER NOT NULL CHECK(sequence>=0), start_ms INTEGER NOT NULL CHECK(start_ms>=0),
 end_ms INTEGER NOT NULL CHECK(end_ms>=start_ms), captured_at TEXT, text TEXT NOT NULL,
 normalized_text TEXT, provider TEXT NOT NULL, model TEXT, language TEXT,
 confidence REAL CHECK(confidence IS NULL OR (confidence>=0 AND confidence<=1)),
 is_final INTEGER NOT NULL DEFAULT 1 CHECK(is_final IN (0,1)), raw_response_json TEXT,
 created_at TEXT NOT NULL, UNIQUE(recording_id,sequence)
);
CREATE INDEX idx_transcripts_meeting_time ON transcript_segments(meeting_id,start_ms,sequence);
CREATE TABLE processing_errors (
 id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id TEXT REFERENCES meetings(id) ON DELETE CASCADE,
 recording_id TEXT REFERENCES recordings(id) ON DELETE CASCADE, stage TEXT NOT NULL, sequence INTEGER,
 provider TEXT, code TEXT, message TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}',
 retryable INTEGER NOT NULL DEFAULT 0 CHECK(retryable IN (0,1)), attempt INTEGER,
 occurred_at TEXT NOT NULL, resolved_at TEXT
);
CREATE INDEX idx_errors_meeting_time ON processing_errors(meeting_id,occurred_at);
CREATE TABLE intelligence_insights (
 id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, status TEXT NOT NULL, text TEXT NOT NULL, owner TEXT, due_at TEXT,
 confidence REAL CHECK(confidence IS NULL OR (confidence>=0 AND confidence<=1)),
 style_json TEXT NOT NULL DEFAULT '{}', source_start_sequence INTEGER, source_end_sequence INTEGER,
 provider TEXT, model TEXT, dedupe_key TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
 supersedes_id TEXT REFERENCES intelligence_insights(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(meeting_id,dedupe_key)
);
CREATE INDEX idx_insights_meeting_status ON intelligence_insights(meeting_id,status,updated_at);
CREATE TABLE insight_revisions (
 insight_id TEXT NOT NULL REFERENCES intelligence_insights(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(insight_id,revision)
);
CREATE TABLE diagram_operations (
 id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 insight_id TEXT REFERENCES intelligence_insights(id) ON DELETE SET NULL, ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 operation_type TEXT NOT NULL CHECK(operation_type IN ('create','update','delete','style','noop')),
 target_id TEXT, request_json TEXT NOT NULL, response_json TEXT,
 status TEXT NOT NULL CHECK(status IN ('pending','applied','failed','skipped')),
 attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0), error_message TEXT,
 created_at TEXT NOT NULL, applied_at TEXT, UNIQUE(meeting_id,ordinal)
);
CREATE INDEX idx_diagram_ops_pending ON diagram_operations(status,created_at);
CREATE TABLE diagram_bindings (
 meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 insight_id TEXT NOT NULL REFERENCES intelligence_insights(id) ON DELETE CASCADE,
 element_id TEXT NOT NULL, element_type TEXT, last_operation_id TEXT REFERENCES diagram_operations(id),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(meeting_id,insight_id), UNIQUE(meeting_id,element_id)
);
CREATE TABLE diagram_snapshots (
 id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 after_operation_ordinal INTEGER NOT NULL, document_json TEXT NOT NULL, content_hash TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(meeting_id,after_operation_ordinal)
);
CREATE TABLE processor_checkpoints (
 meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE, processor TEXT NOT NULL,
 last_sequence INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(meeting_id,processor)
);
CREATE TABLE artifacts (
 id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(meeting_id,kind)
);
"""), (2, r"""
CREATE TABLE pipeline_runs (
 id TEXT PRIMARY KEY,
 meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 recording_id TEXT REFERENCES recordings(id) ON DELETE SET NULL,
 correlation_id TEXT NOT NULL UNIQUE,
 started_at TEXT NOT NULL,
 ended_at TEXT,
 status TEXT NOT NULL CHECK(status IN ('running','completed','disconnected','failed')),
 stt_provider TEXT,
 stt_model TEXT,
 intelligence_provider TEXT,
 intelligence_model TEXT,
 segments_seen INTEGER NOT NULL DEFAULT 0 CHECK(segments_seen>=0),
 transcripts_stored INTEGER NOT NULL DEFAULT 0 CHECK(transcripts_stored>=0),
 insights_stored INTEGER NOT NULL DEFAULT 0 CHECK(insights_stored>=0),
 diagram_ops_applied INTEGER NOT NULL DEFAULT 0 CHECK(diagram_ops_applied>=0),
 error_count INTEGER NOT NULL DEFAULT 0 CHECK(error_count>=0),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX idx_pipeline_runs_meeting ON pipeline_runs(meeting_id,started_at);
CREATE INDEX idx_pipeline_runs_status ON pipeline_runs(status,started_at);

CREATE TABLE pipeline_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 run_id TEXT NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
 meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 recording_id TEXT REFERENCES recordings(id) ON DELETE SET NULL,
 event_name TEXT NOT NULL,
 stage TEXT NOT NULL CHECK(stage IN ('meeting','recording','decode','stt','storage','intelligence','diagram','export','websocket')),
 status TEXT NOT NULL CHECK(status IN ('started','succeeded','failed','retried','skipped','cancelled')),
 sequence INTEGER,
 attempt INTEGER,
 provider TEXT,
 model TEXT,
 duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms>=0),
 queue_depth INTEGER CHECK(queue_depth IS NULL OR queue_depth>=0),
 input_count INTEGER,
 output_count INTEGER,
 error_code TEXT,
 attributes_json TEXT NOT NULL DEFAULT '{}',
 occurred_at TEXT NOT NULL
);
CREATE INDEX idx_pipeline_events_run ON pipeline_events(run_id,id);
CREATE INDEX idx_pipeline_events_meeting ON pipeline_events(meeting_id,occurred_at);
CREATE INDEX idx_pipeline_events_stage ON pipeline_events(stage,status,occurred_at);
CREATE INDEX idx_pipeline_events_failures ON pipeline_events(run_id,id) WHERE status='failed';

ALTER TABLE processing_errors ADD COLUMN run_id TEXT REFERENCES pipeline_runs(id) ON DELETE SET NULL;
ALTER TABLE processing_errors ADD COLUMN event_id INTEGER REFERENCES pipeline_events(id) ON DELETE SET NULL;
ALTER TABLE processing_errors ADD COLUMN error_class TEXT;
"""), (3, r"""
CREATE TABLE transcript_corrections (
 id TEXT PRIMARY KEY,
 transcript_id INTEGER NOT NULL REFERENCES transcript_segments(id) ON DELETE CASCADE,
 meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL CHECK(revision>0),
 raw_text TEXT NOT NULL,
 corrected_text TEXT NOT NULL,
 changed INTEGER NOT NULL CHECK(changed IN (0,1)),
 edits_json TEXT NOT NULL DEFAULT '[]',
 unresolved_json TEXT NOT NULL DEFAULT '[]',
 confidence REAL CHECK(confidence IS NULL OR (confidence>=0 AND confidence<=1)),
 provider TEXT NOT NULL,
 model TEXT NOT NULL,
 prompt_version TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(transcript_id,revision)
);
CREATE INDEX idx_transcript_corrections_meeting ON transcript_corrections(meeting_id,transcript_id,revision);
"""), (4, r"""
CREATE TABLE architecture_graphs (
 meeting_id TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
 version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0),
 graph_json TEXT NOT NULL DEFAULT '{"entities":{},"relationships":{},"questions":{}}',
 updated_at TEXT NOT NULL
);
CREATE TABLE architecture_mutations (
 id TEXT PRIMARY KEY,
 meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 base_version INTEGER NOT NULL CHECK(base_version>=0),
 resulting_version INTEGER NOT NULL CHECK(resulting_version>base_version),
 operation TEXT NOT NULL,
 target_id TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 provenance TEXT NOT NULL,
 confidence REAL NOT NULL CHECK(confidence>=0 AND confidence<=1),
 evidence_json TEXT NOT NULL,
 agent_run_id TEXT,
 created_at TEXT NOT NULL,
 UNIQUE(meeting_id,ordinal), UNIQUE(meeting_id,resulting_version)
);
CREATE INDEX idx_architecture_mutations_meeting ON architecture_mutations(meeting_id,ordinal);
CREATE TABLE architecture_snapshots (
 id TEXT PRIMARY KEY,
 meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 graph_version INTEGER NOT NULL,
 graph_json TEXT NOT NULL,
 content_hash TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(meeting_id,graph_version)
);
CREATE TABLE architecture_agent_runs (
 id TEXT PRIMARY KEY,
 meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 base_graph_version INTEGER NOT NULL,
 transcript_ids_json TEXT NOT NULL,
 provider TEXT NOT NULL,
 model TEXT NOT NULL,
 prompt_version TEXT NOT NULL,
 request_hash TEXT NOT NULL,
 response_json TEXT,
 validation_status TEXT NOT NULL CHECK(validation_status IN ('running','accepted','rejected','failed')),
 error_code TEXT,
 duration_ms INTEGER,
 created_at TEXT NOT NULL,
 completed_at TEXT
);
"""),)
