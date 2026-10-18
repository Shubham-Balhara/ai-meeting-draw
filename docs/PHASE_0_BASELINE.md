# Phase 0 — Baseline and Observability

## Implemented

- Immutable, checksum-verified v1 software-architecture evaluation corpus.
- Deterministic evaluator for concept precision, recall, F1, duplicates, and unsupported outputs.
- Additive SQLite migration v2 with correlated pipeline runs and append-only stage events.
- Run lifecycle, counters, bounded metadata, event validation, and secret/content-key rejection.
- Content-free structured logging helpers and prompt/schema version constants.
- Rollout feature flags for transcript correction, architecture agent v2, and AI Meeting Draw mapper v2.

## Logging levels

- INFO: meeting/run lifecycle and completed stage summaries.
- WARNING: retry, skip, degradation, and disconnect.
- ERROR: terminal failures using error class/code, without provider body or meeting content.
- DEBUG: queue depth, timing, sequence, schema validation, and other diagnostic metadata.

Raw transcript text, prompts, audio, API keys, authorization values, and provider response bodies are forbidden in pipeline-event attributes and removed by the structured log helper.

## Evaluation contract

The corpus contains synthetic cases for default REST, explicit gRPC, Kafka publish/consume, questions and confirmation, generic storage, vendor non-invention, external APIs, retraction, ambiguous pronouns, silence/chitchat, prompt injection, technical-term correction, contradictions, frontend/backend calls, caching, and decisions.

The deterministic reference result verifies the evaluator itself. It is not represented as an AI-provider quality score. Provider baselines are recorded separately per exact provider/model after the architecture schema and prompt implemented in later phases exist.

## Rollback

Disable all three rollout flags. Migration v2 is additive and does not change existing domain rows or the existing meeting execution path. New telemetry tables may remain unused safely.
