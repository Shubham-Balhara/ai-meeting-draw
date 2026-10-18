# Cleanup candidates

This inventory separates the live meeting solution from inherited or optional repository features. Nothing here was deleted automatically. Remove a group only after running backend tests and the frontend typecheck/build.

## Safe to remove when unused

| Path | Purpose | Impact |
|---|---|---|
| `mcp/` | Standalone MCP server for controlling boards | No impact on capture, STT, intelligence, or editor |
| `scripts/railway-sync-env.sh`, `railway.env.map` | Railway environment synchronization | Remove when Railway is unused |
| `.github/workflows/deploy.yml` | Railway deployment | Remove for manual/other hosting |
| `.github/workflows/ghcr.yml` | Container publishing | Remove when images are not published |
| `scripts/record-demo.mjs` | Demo recording automation | No runtime impact |
| `docs/media/` | Historical screenshots/demo media | Update any links first |
| `web/t6.txt` | Development scratch artifact | No runtime impact |

## Optional features requiring coordinated refactoring

These are not required by meeting-to-architecture, but remain imported or exposed by the editor. Do not delete individual files without removing their routes and UI references.

| Group | Paths | Follow-up |
|---|---|---|
| Original board AI/jobs | `backend/app/api/ai.py`, `backend/app/services/ai.py`, `backend/app/services/ai_jobs.py`, `backend/app/services/pool.py`, `web/src/features/ai/`, `web/src/features/generate/`, `web/src/features/jobs/` | Remove router registration, UI/routes/state, and tests together |
| Comments | comment API/service/storage and `web/src/state/commentsStore.ts` | Remove editor comment UI and API wiring together |
| PostgreSQL adapters | PostgreSQL repositories/ledgers and tests | Retain only file/SQLite settings; remove compose DB |
| S3 log shipping | `backend/app/services/object_storage.py`, `backend/app/services/log_rotation.py` | Remove startup wiring and S3 variables |
| Contracts | `contracts/` and contract-only tests | Remove only if contract validation is not part of CI |

## Legacy meeting pipeline

`backend/app/meeting/intelligence.py`, `schema.py`, and `diagram.py` support the v1 fallback selected when `ENABLE_ARCHITECTURE_AGENT_V2=false`. The verified setup uses v2, but `api.py` still imports that fallback. To remove it safely:

1. Remove the v1 branch/imports from `api.py`.
2. Keep schema types shared with correction/provider code.
3. Remove v1-only tests after confirming equivalent v2 coverage.
4. Run all tests and one live capture before deleting modules.

`backend/app/meeting/providers.py` is shared by v2 and transcript correction and must remain.

## Required by the current solution

- `backend/app/meeting/` v2 architecture, correction, storage, STT, layout, and API modules
- document repository/service, SVG renderer, sanitizer, and `backend/app/api/collab.py`
- React diagram, editor, state, and collaboration code
- `backend/eval/`, fixtures, and meeting tests while prompts evolve
- `tools/meeting-capture/` for local end-to-end verification

## Parent-folder cleanup

After verifying the unified repository, the old sibling `meeting-audio/backend/` and `meeting-audio/frontend/` are redundant. The capture page is now inside this repository. Preserve any `.env`, recordings, transcripts, or databases needed before deleting those folders.
