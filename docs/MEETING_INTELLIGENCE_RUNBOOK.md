# Meeting Intelligence v2 Runbook

## Safe activation choice

The implementation is installed behind feature flags. Enabling the architecture agent sends transcript text to `INTELLIGENCE_PROVIDER`. Enabling correction sends transcript text to `CORRECTION_PROVIDER`. Use local Qwen when meeting content must remain on this Mac.

### Recommended local profile

```env
LOG_LEVEL=DEBUG
INTELLIGENCE_ENABLED=true
INTELLIGENCE_PROVIDER=qwen
INTELLIGENCE_MODEL=qwen2.5:7b
INTELLIGENCE_BASE_URL=http://127.0.0.1:11434

ENABLE_TRANSCRIPT_CORRECTION=true
CORRECTION_PROVIDER=qwen
CORRECTION_MODEL=qwen2.5:7b
CORRECTION_CONFIDENCE_THRESHOLD=0.78
CORRECTION_QUEUE_SIZE=32

ENABLE_ARCHITECTURE_AGENT_V2=true
ENABLE_DIAGRAM_MAPPER_V2=true
PROMPT_LOGGING=false
```

Confirm the installed model name with `ollama list`; replace `qwen2.5:7b` with that exact name. Start Ollama before the backend.

### Cloud profile

Only use this profile when participants and your data policy permit sending transcript text to that provider:

```env
ENABLE_TRANSCRIPT_CORRECTION=true
CORRECTION_PROVIDER=groq
CORRECTION_MODEL=llama-3.3-70b-versatile
ENABLE_ARCHITECTURE_AGENT_V2=true
```

`INTELLIGENCE_API_KEY`, `INTELLIGENCE_PROVIDER`, `INTELLIGENCE_MODEL`, and `INTELLIGENCE_BASE_URL` continue to select Gemini, xAI Grok, GroqCloud, or Qwen. Provider/model availability and free quotas change; verify them in the provider console before a demo.

## Start and verify

```bash
cd /path/to/ai-meeting-draw
source .venv/bin/activate
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Serve `tools/meeting-capture/` as described in the README, start capture, select the Meet tab with tab audio enabled, and speak these checks in order:

1. “Create a product service and a checkout service.”
2. “The product service calls checkout.” The edge should appear as muted/dotted `REST/HTTPS (default)`.
3. “Use gRPC for that call.” The same edge should update rather than duplicate.
4. “Should we add Redis for product data?” This must appear as an open red question, not a confirmed cache.
5. “Yes, use Redis for product data.” The same idea should become confirmed.
6. “Orders publishes order-created events to Kafka and fulfillment consumes them.” This should create distinct publish and consume directions.
7. “Make a synchronous call through Kafka.” This contradiction should raise a question and must not silently change topology.

Browser events are `transcript`, `transcript_corrected`, `architecture_updated`, `architecture_error`, and `completed`. The completion export contains immutable raw transcript rows plus correction revisions. The architecture graph and replay journal are in the meeting SQLite database.

## Test commands

```bash
cd /path/to/ai-meeting-draw
.venv/bin/python -m pytest backend/tests backend/app/meeting/tests -q
cd web
npm run typecheck
npm run build
```

## Rollback

Set these flags to `false` and restart:

```env
ENABLE_TRANSCRIPT_CORRECTION=false
ENABLE_ARCHITECTURE_AGENT_V2=false
ENABLE_DIAGRAM_MAPPER_V2=false
```

All schema migrations are additive. Existing raw audio, transcripts, corrections, architecture mutations, and audit events remain readable. The older generic intelligence path remains available when `INTELLIGENCE_ENABLED=true` and `ENABLE_ARCHITECTURE_AGENT_V2=false`.

## Known operational notes

- The browser still preserves the mixed Meet+microphone WebM while processing separately captured source tracks through the Web Audio mix. Server-side STT receives the mixed stream.
- VAD is energy-based for provider-neutral utterance boundaries; WhisperX also performs its own VAD.
- The lane layout is deterministic and stable, not a full ELK graph optimizer. Existing/manual positions are never moved automatically.
- FastAPI reports deprecation warnings for `on_event`; they do not affect current behavior but should be migrated to lifespan hooks in routine maintenance.
- The production frontend build reports a bundle-size advisory. It is unrelated to meeting intelligence correctness.
