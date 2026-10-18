# Meeting intelligence integration

AI Meeting Draw is the single backend for meeting audio ingestion, transcription,
intelligence, durable transcript storage, and live diagram updates. Meeting
code remains isolated in `backend/app/meeting/`; AI Meeting Draw's existing document
repository and collaboration protocol are unchanged.

## Data flow

1. `WS /ws/meetings/{external_id}/audio` accepts the existing start frame,
   followed by the continuous WebM/Opus byte stream.
2. The original stream is preserved while one ffmpeg process decodes mono
   16 kHz PCM.
3. WhisperX or Gemini produces ordered transcript segments.
4. Every segment is committed to SQLite before browser delivery or
   intelligence processing.
5. A sequential coordinator batches committed segments and asks the selected
   intelligence provider for strict semantic mutations.
6. The deterministic adapter maps semantics to stable AI Meeting Draw node/edge IDs,
   layout and styles, saves the complete board, and broadcasts the page.
7. Stop drains the final STT segment and intelligence batch, then atomically
   exports TXT and JSON transcripts from SQLite.

The `ready` event includes `session_id`, `board_id`, and `board_url`. Open that
URL while the meeting is active to watch the diagram evolve.

## Intelligence input and why each field matters

Providers receive a bounded `AnalysisRequest`, not raw audio or the entire
meeting:

- `meeting_id`: isolates state and stable identities.
- `transcripts`: recent ordered text with sequence and media-relative times.
- `new_sequence_ids`: identifies the unprocessed portion of the window.
- `active_insights`: prevents unchanged facts from being emitted repeatedly.
- `active_links`: lets relationships transition instead of duplicating.

The model returns `AnalysisResult` with insight and relationship mutations.
`semantic_key` is the idempotency key; `status` drives lifecycle styling;
`kind` selects the visual role; `evidence_sequences` makes every mutation
auditable; and relationships become connectors. The model never chooses
coordinates, colors, AI Meeting Draw IDs, or arbitrary board JSON.

## Visual convention

| Meaning | AI Meeting Draw representation |
|---|---|
| Tentative proposal | Grey, dotted rounded node |
| Open question | Red callout with dashed border |
| Confirmed decision | Normal blue solid decision node |
| Action item | Amber sticky with owner/due date |
| Risk or blocker | Orange warning note |
| Resolved question | Updated in place to normal green solid form |
| Rejected or superseded | Muted, semi-transparent and dashed |
| Tentative relationship | Dotted connector |
| Confirmed relationship | Solid connector |

AI Meeting Draw already supports these fields (`strokeDash`, fill/stroke/text colors,
opacity, node kinds, arrow heads, labels and animated flows); no renderer fork
is required.

## Provider configuration

Copy `ai-meeting-draw/.env.example` to `ai-meeting-draw/.env`. During migration, the
old `meeting-audio/backend/.env` is also read as a fallback.

Gemini:

```dotenv
INTELLIGENCE_ENABLED=true
INTELLIGENCE_PROVIDER=gemini
INTELLIGENCE_MODEL=gemini-3.8-flash
INTELLIGENCE_API_KEY=
GEMINI_API_KEY=your_key
```

Grok:

```dotenv
INTELLIGENCE_ENABLED=true
INTELLIGENCE_PROVIDER=grok
INTELLIGENCE_MODEL=your-accessible-grok-model
INTELLIGENCE_API_KEY=your_xai_key
INTELLIGENCE_BASE_URL=https://api.x.ai/v1
```

The Grok model remains explicit because model access varies by xAI account.

Local Qwen through Ollama:

```bash
ollama pull qwen3:4b
ollama serve
```

```dotenv
INTELLIGENCE_ENABLED=true
INTELLIGENCE_PROVIDER=qwen
INTELLIGENCE_MODEL=qwen3:4b
INTELLIGENCE_API_KEY=
INTELLIGENCE_BASE_URL=http://127.0.0.1:11434
```

No cross-provider fallback occurs implicitly. Changing provider can change
privacy, price and behavior, so fallback must be an explicit future policy.

## Run the unified backend

```bash
cd /path/to/ai-meeting-draw
cp .env.example .env
cd web
npm ci
npm run build
cd ..
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal, serve the existing capture page:

```bash
cd /path/to/ai-meeting-draw
python3 -m http.server 5173 --directory tools/meeting-capture
python3 -m http.server 3000
```

Open `http://localhost:3000`. AI Meeting Draw itself is at `http://localhost:8000`.

Health and stored-session inspection:

```bash
curl http://127.0.0.1:8000/api/meetings/health
curl http://127.0.0.1:8000/api/meetings/<session-id>
```

Generated data defaults to:

- SQLite: `ai-meeting-draw/backend/data/meetings.sqlite3`
- WebM recordings: `ai-meeting-draw/backend/recordings/`
- Transcript TXT/JSON: `ai-meeting-draw/backend/transcripts/`
- AI Meeting Draw boards: the existing file/Postgres document repository

SQLite uses WAL, foreign keys, bounded serialized access, idempotent transcript
keys, immutable insight revisions, diagram-operation outbox records, and
checksummed atomic transcript exports.

## Operational constraints

- Run one Uvicorn worker for the hackathon. Collaboration rooms and per-board
  locks are process-local.
- The board JSON limit is 1 MB. Full transcripts stay in SQLite; only concise
  distilled insights go on the board.
- A AI Meeting Draw board URL is an anonymous edit capability and should be treated as
  a secret.
- Transcript data may be sensitive. Local SQLite is not encrypted at rest.
- WhisperX may print a TorchCodec/ffmpeg-version warning. This path supplies
  pre-decoded PCM arrays, so TorchCodec is not used.
