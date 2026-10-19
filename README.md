# AI Meeting Draw

AI Meeting Draw turns a live software-design conversation into a persistent transcript and an evolving architecture diagram. A browser helper captures Google Meet tab audio plus the local microphone, streams WebM/Opus to FastAPI, and receives ordered transcript and architecture events. The backend preserves the original recording, continuously decodes audio to 16 kHz PCM, transcribes it, stores meeting state in SQLite, asks a configurable intelligence provider to maintain an architecture graph, and publishes diagram updates to the collaborative board.

The drawing editor is adapted from [Noddle Draw](https://github.com/noddle-dev/noddle-draw). Its structured board, SVG renderer, document persistence, and collaboration channel are retained and integrated with the meeting pipeline.

## What is included

- Google Meet tab audio and microphone capture through an optional local browser page
- Concurrent `.webm` recording preservation and continuous FFmpeg decoding
- WhisperX/local Whisper or Gemini speech-to-text, selected with `STT_PROVIDER`
- Silence handling, ordered asynchronous transcription, and live WebSocket events
- Optional transcript correction for domain-aware cleanup
- Stateful architecture agent using Gemini, xAI Grok, GroqCloud, or local Qwen/Ollama
- Architecture defaults, provenance, validation, incremental layout, and live board updates
- SQLite meeting/transcript storage plus Markdown and JSON transcript export
- React collaborative diagram editor served by the same FastAPI application

## Requirements

- macOS with Chrome or another Chromium browser
- Python 3.12 (recommended; Python 3.14 is not supported by all ML dependencies)
- Node.js 20+ and npm
- FFmpeg (`brew install ffmpeg`)
- A working WhisperX environment, or a Google AI Studio key for Gemini STT
- Optional: Ollama for local Qwen intelligence (`brew install ollama`)

## 1. Configure

```bash
cd /path/to/ai-meeting-draw
cp .env.example .env
```

Edit `.env`. For an existing local WhisperX installation, set `WHISPERX_PYTHON` to the Python executable inside that environment. Keep API keys only in `.env`; it is ignored by Git.

The sample configuration uses:

```dotenv
STT_PROVIDER=whisper
INTELLIGENCE_PROVIDER=qwen
INTELLIGENCE_MODEL=qwen3:8b
INTELLIGENCE_BASE_URL=http://127.0.0.1:11434
ENABLE_ARCHITECTURE_AGENT_V2=true
ENABLE_DIAGRAM_MAPPER_V2=true
LOG_LEVEL=DEBUG
```

For Gemini through Vertex AI, use `STT_PROVIDER=gemini`, `GEMINI_BACKEND=vertex`,
`GOOGLE_CLOUD_PROJECT`, and `GOOGLE_CLOUD_LOCATION=global`. Cloud Run uses its runtime service
account, so no API key is required. For the Gemini Developer API instead, set
`GEMINI_BACKEND=developer` and `GEMINI_API_KEY`.

For GroqCloud intelligence, use provider `groq`, `https://api.groq.com/openai/v1`, a supported Groq model, and the Groq API key. Provider `grok` means the separate xAI API.

## 2. Install and run the backend

```bash
cd /path/to/ai-meeting-draw/backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

The repository-level `.env` is the single configuration source. Confirm startup at <http://127.0.0.1:8000/api/meetings/health>.

## 3. Run local Qwen (optional)

```bash
ollama list
ollama pull qwen3:8b
ollama serve
```

If Ollama is already running as a background service, do not start another server. Verify its API in a second terminal:

```bash
curl http://127.0.0.1:11434/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:8b","stream":false,"messages":[{"role":"user","content":"Reply with OK"}]}'
```

## 4. Build the diagram editor

```bash
cd /path/to/ai-meeting-draw/web
npm ci
npm run build
```

Restart FastAPI after the first build, then open <http://127.0.0.1:8000>. During frontend development, run `npm run dev` and open the Vite URL instead.

## 5. Verify a live meeting locally

The optional helper in `tools/meeting-capture/index.html` is included only for local verification. Serve it from HTTP because browser media APIs may not work from `file://`:

```bash
cd /path/to/ai-meeting-draw
python3 -m http.server 5173 --directory tools/meeting-capture
```

Open <http://127.0.0.1:5173>, confirm the backend URL and meeting ID, then:

1. Click **Start capture**.
2. Select the Google Meet tab and enable **Share tab audio**.
3. Allow microphone access.
4. Say: “The product service calls the checkout service over REST. Checkout stores orders in MySQL and publishes order-created events to Kafka for the notification service.”
5. Follow **Open live board** to watch the diagram update.
6. Click **Stop** and wait for the `completed` event.

The page shows transcript, correction, architecture, and error events. It also keeps a local browser recording for playback/download.

## Data and output locations

Unless overridden in `.env`:

- SQLite: `backend/data/meetings.sqlite3`
- Server recordings: `backend/recordings/`
- Transcript exports: `backend/transcripts/`
- Board/document storage: `backend/storage/`

These paths, SQLite sidecars, recordings, logs, caches, and transcript exports are ignored by Git.

## Verification

```bash
cd /path/to/ai-meeting-draw/backend
source .venv/bin/activate
python -m pytest tests -q

cd ../web
npm run typecheck
npm run build
```

Inspect a completed meeting with `GET /api/meetings/{session_id}`. The WebSocket `ready` event contains the session ID.

## Docker

Run the editor/backend and PostgreSQL with `docker compose up --build`. The ML-heavy WhisperX environment is intentionally external; native local development should point `WHISPERX_PYTHON` to its environment. Gemini STT avoids the local ML dependency.

## Operational notes

- Audio endpoint: `ws://127.0.0.1:8000/ws/meetings/{meeting-id}/audio`
- Send JSON `start`, then binary WebM/Opus chunks, then JSON `stop`.
- Transcription runs outside the receive loop and results are emitted in sequence order.
- Empty segments are ignored; failures are recorded without discarding the original recording.
- The agent uses accumulated graph state plus transcript evidence; the mapper validates operations before changing the board.
- Keep `PROMPT_LOGGING=false` outside controlled debugging because prompts can contain meeting content.

See `docs/meeting-intelligence.md`, `docs/MEETING_INTELLIGENCE_RUNBOOK.md`, and `docs/CLEANUP_CANDIDATES.md` for more detail.

For the hackathon Google Cloud deployment—Cloud Run, Gemini Secret Manager configuration, and Firestore persistence—follow `docs/CLOUD_RUN_DEPLOYMENT.md` or run `scripts/deploy-cloud-run.sh`.

## License

MIT. See `LICENSE`. Preserve upstream copyright and license notices when redistributing derived editor code.
