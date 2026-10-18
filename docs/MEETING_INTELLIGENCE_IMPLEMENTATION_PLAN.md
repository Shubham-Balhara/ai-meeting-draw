# Meeting Intelligence and Architecture Drawing — Implementation Plan

**Status:** Approved design, implementation not started  
**Plan version:** 1.0  
**Execution rule:** Implement phases in order. A phase is complete only when every acceptance criterion and its exit gate pass. Do not begin the next phase merely because the main code path appears to work.

## 1. Objective

Turn live meeting audio into a trustworthy, continuously updated software architecture diagram while preserving the complete evidence trail:

1. capture clean meeting audio;
2. produce timestamped raw speech-to-text;
3. conditionally correct obvious transcription errors without inventing meaning;
4. interpret the corrected statements in a software-system-design context;
5. maintain a typed, stateful architecture model with explicit provenance;
6. validate all changes deterministically;
7. render the model through AI Meeting Draw using meaningful shapes, relationships, styles, and stable layout;
8. retain raw audio, raw/corrected transcripts, model decisions, diagram mutations, and meeting exports.

The intelligence layer must be provider-independent. Gemini, Groq, and local Qwen may implement the same contracts, but provider output is never trusted until schema and semantic validation pass.

## 2. Non-goals and safety boundaries

- The model is not allowed to invent business requirements, named technologies, vendors, protocols, capacities, security controls, or deployment details that were not spoken or selected in a project profile.
- Transcript correction is not architecture reasoning. It may repair wording; it may not add design facts.
- The architecture agent expresses semantic intent; it does not choose canvas coordinates or arbitrary visual styling.
- AI Meeting Draw is the renderer/editor, not the source of architectural truth. The typed architecture graph and its mutation history are authoritative.
- A plausible inference is not a confirmed fact. Defaults and inferences must remain visibly distinguishable and reversible.
- Silence, low-confidence noise, and unintelligible audio must not create diagram changes.
- Existing confirmed elements are never silently deleted or replaced. Retraction, correction, or supersession requires evidence and an auditable mutation.
- Provider failure must not stop recording, raw transcript persistence, or meeting cleanup.

## 3. Design principles

1. **Evidence before mutation:** every semantic change cites transcript segment IDs.
2. **Preserve raw truth:** never overwrite raw STT text, timing, or audio references.
3. **Separate concerns:** STT, text correction, semantic interpretation, validation, layout, and rendering have separate contracts.
4. **Deterministic defaults:** common omissions are resolved by a versioned policy, not model improvisation.
5. **Stable identity:** mentions such as “checkout,” “checkout service,” and “it” resolve to stable entity IDs where evidence supports it.
6. **Minimal change:** each utterance produces the smallest graph patch justified by the evidence.
7. **Visible uncertainty:** tentative, suggested, questioned, contradicted, and confirmed states are visually distinct.
8. **Idempotency:** replaying the same transcript event must not duplicate nodes or edges.
9. **Provider parity:** all intelligence providers receive the same structured inputs and must produce the same schema.
10. **Measured quality:** decisions are evaluated against a fixed test corpus, not subjective demo impressions alone.

## 4. Target pipeline

```text
Meet tab + microphone
        |
        v
Capture / cleanup / level monitoring -------> preserved WebM recording
        |
        v
Continuous 16 kHz PCM + utterance-aware VAD
        |
        v
STT provider (WhisperX or Gemini)
        |
        +--> immutable raw transcript + confidence metadata
        |
        v
Conditional transcript corrector
        |
        +--> corrected text + edit spans + uncertainty + prompt/model version
        |
        v
Stateful architecture agent
        |
        +--> typed graph mutations + evidence + provenance
        |
        v
Deterministic policy / schema / semantic validator
        |
        +--> accept | reject | repair request | clarification
        |
        v
Architecture graph + mutation journal in SQLite
        |
        v
Deterministic AI Meeting Draw mapper + topology-aware layout
        |
        v
Live diagram patches returned to the browser
```

## 5. Canonical data layers

The implementation must keep these records separate:

| Layer | Purpose | Mutable? |
|---|---|---|
| Audio artifact | Replay and audit | No; finalize once |
| Raw transcript | Exact STT provider output | No |
| Corrected transcript | Conservative text repair | Append revisions; never overwrite raw |
| Architecture entity/relationship | Current semantic state | Yes, through versioned mutations |
| Evidence/provenance | Why each fact exists | Append-only |
| Diagram binding | Architecture ID to AI Meeting Draw object ID | Yes, stable mapping |
| Layout state | User/model positions and grouping | Yes; preserve manual edits |
| Agent run | Inputs, output, provider, prompt version, validation | Append-only |

## 6. Sequential implementation phases

### Phase 0 — Baseline, fixtures, and observability

**Goal:** establish measurable current behavior before changing the pipeline.

**Work:**

1. Create a versioned evaluation corpus containing clean audio, noise, pauses, corrections, homophones, service aliases, pronouns, explicit protocols, implicit calls, contradictions, retractions, and confirmation statements.
2. Manually author reference transcripts and reference architecture graphs for every fixture.
3. Record current metrics: word error rate, technical-term recall, entity/relationship precision and recall, duplicate rate, unsupported-fact rate, mutation latency, diagram stability, and provider cost/tokens.
4. Introduce correlation IDs: `meeting_id`, `audio_segment_id`, `transcript_id`, `correction_run_id`, `agent_run_id`, and `mutation_id`.
5. Add structured logging conventions. INFO records lifecycle and accepted/rejected changes; DEBUG records prompts with secrets removed, schemas, candidate matches, validation details, and timing.
6. Add feature flags so every future phase can be enabled independently and rolled back.

**Configuration:** `LOG_LEVEL`, `EVAL_FIXTURE_DIR`, `ENABLE_TRANSCRIPT_CORRECTION`, `ENABLE_ARCHITECTURE_AGENT_V2`, `ENABLE_DIAGRAM_MAPPER_V2`, `PROMPT_LOGGING`.

**Deliverables:** fixture manifest, scoring script, baseline report, log field specification.

**Exit gate:** the same fixture run is repeatable; failures identify the exact stage and correlation IDs; baseline results are stored. No subsequent phase starts before this gate passes.

### Phase 1 — Audio capture and preprocessing quality

**Goal:** improve the signal presented to STT without risking recording loss.

**Work:**

1. Prefer separate microphone and tab-audio tracks for processing; retain the mixed WebM artifact for playback.
2. Request browser microphone echo cancellation, noise suppression, and automatic gain control as configurable options.
3. Add input-level, clipping, silence, and dropped-chunk metrics.
4. Maintain one continuous ffmpeg decoder per session and emit normalized mono 16 kHz PCM.
5. Replace overlapping fixed windows with utterance-aware VAD segments. Retain configurable minimum/maximum duration and small pre/post speech padding.
6. Deduplicate only at segment boundaries; never discard unique speech merely because it resembles prior text.
7. Treat “no active speech” as a normal ignored segment at DEBUG level, with aggregate counts at INFO on session completion.

**Suggested defaults:** 300–500 ms speech padding; 20–30 second maximum utterance; browser processing enabled for microphone; raw capture always preserved.

**Tests:** silence, background fan, music, tab-only speech, mic-only speech, simultaneous speakers, clipping, reconnect, abrupt browser close.

**Exit gate:** no regression in recording playback; silent segments create no error and no transcript; fixture STT quality or technical-term recall improves without unacceptable latency.

### Phase 2 — STT provider quality and confidence contract

**Goal:** give every downstream stage richer, comparable STT evidence.

**Work:**

1. Extend the provider-neutral result with text, start/end time, language, average log probability, no-speech probability when available, model, provider, prompt/glossary version, and segment boundary metadata.
2. Preserve WhisperX `avg_logprob` instead of discarding it.
3. Add an initial prompt describing software architecture vocabulary without asserting facts about the current meeting.
4. Generate session hotwords from the configured glossary plus names already confirmed in the architecture graph.
5. Make WhisperX VAD onset/offset, model, batch size, language, and context behavior configurable.
6. Benchmark candidate local models on the Phase 0 corpus; select by technical-term recall and latency, not general WER alone.
7. Normalize Gemini STT output to the same contract and retain provider-specific metadata separately.
8. Add silence/noise thresholds and an explicit `ignored_reason` rather than representing ignored audio as an exception.

**Tests:** Kafka/Cafka, Redis/reddish, Kubernetes, gRPC, GraphQL, PostgreSQL, product/project service, checkout/check out, service aliases, and named acronyms.

**Exit gate:** provider outputs pass the common schema; raw results are persisted before downstream processing; selected STT profiles meet the recorded quality/latency target.

### Phase 3 — Conservative transcript correction

**Goal:** improve damaged STT text using local context while preserving uncertainty and original evidence.

**Trigger policy:** do not call the corrector for every segment by default. Trigger when confidence is low, glossary terms are near-matches, grammar is damaged, overlapping alternatives disagree, a new term conflicts with known names, or the architecture agent requests clarification. A configuration may force correction for evaluation.

**Input:** raw segment, confidence fields, nearby raw/corrected segments, active glossary, confirmed entity aliases, optional alternative hypotheses, and project domain profile.

**Output:** `raw_text`, `corrected_text`, `changed`, edit spans with reasons, unresolved spans, confidence, and provider/model/prompt version.

**Persistence:** correction is a new append-only record linked to raw transcript. A later correction creates another revision.

**Exit gate:** on the golden corpus, correction improves technical-term accuracy and does not exceed the agreed semantic-change/introduced-error threshold. Every changed token is explainable, and an unchanged answer is supported.

### Phase 4 — Typed architecture intermediate representation and SQLite migration

**Goal:** replace generic insights with an architecture-specific source of truth.

**Core entities:** person/actor, client, browser app, mobile app, frontend, backend service, microservice, API gateway, load balancer, database, cache, object storage, queue, topic, message broker, external system, third-party API, boundary/group, and unknown component.

**Core relationships:** synchronous call, asynchronous publish, asynchronous consume, read, write, cache read/write, authentication/authorization, file transfer, event flow, dependency, containment, replication, and unknown relationship.

**Required fields:** stable ID, canonical name, aliases, type, description, status, confidence, provenance (`explicit`, `defaulted`, `inferred`, `suggested`, `questioned`, `confirmed`, `retracted`), evidence transcript IDs, attributes, created/updated sequence, and version.

**Mutation operations:** create, update, merge aliases, connect, update relationship, confirm, mark tentative, question, supersede, retract, and no-op.

**Database work:** migrations for transcript revisions, architecture snapshots, entities, relationships, evidence links, mutation journal, open questions, provider runs, diagram bindings, and layout state. Existing transcript rows remain compatible and exportable.

**Exit gate:** graph state can be reconstructed exactly by replaying mutations; foreign keys and uniqueness prevent dangling edges and duplicate stable IDs; migration and rollback are tested on a copy of existing data.

### Phase 5 — Deterministic defaults, entity resolution, and validator

**Goal:** move repeatable decisions out of the model and block unsupported output.

**Work:**

1. Implement canonical name normalization and alias matching.
2. Resolve pronouns only when a single recent compatible antecedent exists; otherwise create a clarification, not a guess.
3. Apply the project default policy in Section 9.
4. Validate entity and relationship types, endpoints, evidence, provenance, confidence, and allowed state transitions.
5. Detect duplicates, cycles where relevant, contradictory protocols, self-links, missing endpoints, and invalid containment.
6. Make replay idempotent using transcript/event IDs plus mutation fingerprints.
7. Return machine-readable validation failures suitable for one bounded repair call.

**Exit gate:** deterministic unit tests cover every default and conflict rule; malformed or unsupported model output cannot change the graph; replay creates no duplicates.

### Phase 6 — Stateful architecture agent

**Goal:** interpret each corrected utterance against the complete current architecture state and propose minimal typed mutations.

**Implementation:**

1. Supply a compact current graph, recent conversation, unresolved questions, project policy, allowed schema, and AI Meeting Draw capability summary.
2. Give the agent read tools (`get_architecture`, `find_entity`, `get_evidence`, `get_open_questions`, `get_render_capabilities`) and proposal tools (`propose_mutations`, `raise_question`, `resolve_question`). Tools propose; only the validator commits.
3. Use structured output with strict enums and no free-form diagram JSON.
4. Run one principal architecture call per meaningful utterance batch. Use one repair call only after schema/semantic rejection. Do not loop indefinitely.
5. Store provider, model, prompt version, input state version, output, validation result, latency, and tokens.
6. Serialize commits by meeting sequence. Parallel provider calls may finish out of order, but their mutations must be rebased/revalidated against the latest graph.

**Exit gate:** the Phase 0 scenarios generate correct nodes and links, update existing identities rather than duplicating them, distinguish defaults from explicit facts, and introduce no unsupported named technology.

### Phase 7 — AI Meeting Draw semantic capability contract

**Goal:** make rendering comprehensive and deterministic rather than asking the model to improvise shapes.

**Work:**

1. Implement the mappings in Sections 10 and 11 as a versioned renderer policy.
2. Keep stable architecture-to-AI Meeting Draw bindings.
3. Support nodes, groups, edge labels, routing, heads, dash patterns, questions, decisions, and confidence/provenance styles.
4. Validate that every emitted shape and edge option is supported by the installed AI Meeting Draw version.
5. Add compatibility fallbacks: unsupported specialized node -> `component`; unsupported edge style -> basic arrow while preserving semantic metadata.

**Exit gate:** snapshot tests cover every entity type, relationship type, status, fallback, and update transition. No architecture entity is lost because a specialized visual is unavailable.

### Phase 8 — Topology-aware layout and incremental rendering

**Goal:** produce readable diagrams that remain stable during a meeting.

**Work:**

1. Use a graph layout engine such as ELK or Dagre for initial placement.
2. Apply default lanes: actors/clients left; edge/gateway next; services center; owned data stores below services; brokers between publishers and consumers; external systems right; boundaries around domains.
3. Preserve existing and user-moved positions. Lay out only new/unplaced elements unless the user requests reflow.
4. Minimize crossings, overlaps, long reverse edges, and label collisions.
5. Animate only active flow/highlight when useful; do not make permanent architecture edges continuously distracting.
6. Apply diagram changes as atomic patches so partial rendering cannot corrupt the canvas.

**Exit gate:** representative graphs remain readable at 5, 15, and 40 nodes; adding one service does not unnecessarily move the entire diagram; user positions survive later updates.

### Phase 9 — Browser workflow, recovery, and meeting lifecycle

**Goal:** make uncertainty, changes, failures, and finalization understandable to the user.

**Work:**

1. Stream stage-specific events: raw transcript, corrected transcript, proposed architecture change, accepted change, rejected change, open question, resolved question, and render patch.
2. Show why a correction or graph change occurred and link it to meeting time.
3. Allow confirm, reject, edit, merge, and resolve-question actions.
4. Keep recording/STT alive when intelligence or rendering providers fail; retry from persisted records.
5. Finalize recordings, drain ordered queues, persist final graph, and export raw/corrected transcript plus architecture decision log.
6. Recover abandoned sessions and mark incomplete provider runs.

**Exit gate:** forced provider/network/browser failures lose neither raw recording nor persisted transcript; recovery resumes without duplicate graph changes; meeting export is reproducible from SQLite.

### Phase 10 — Evaluation, provider comparison, and release hardening

**Goal:** select safe defaults and prove the end-to-end system is demonstrably better.

**Work:**

1. Compare Gemini, Groq, and Qwen on the same correction and architecture fixtures.
2. Measure correction precision, technical-term recall, entity/edge F1, unsupported-fact rate, duplicate rate, question quality, latency, cost, and JSON/validator failure rate.
3. Add regression thresholds to automated tests.
4. Run long-meeting, concurrency, disconnect, SQLite locking, provider quota, and malformed-output tests.
5. Document production and hackathon profiles, provider/model availability checks, privacy implications, and fallback order.

**Exit gate:** selected profiles meet agreed thresholds; all secrets stay server-side; provider degradation is recoverable; setup and demo runbooks pass on a clean machine.

## 7. Transcript correction agent — instruction contract v1

This is the required content of the versioned correction prompt. Provider adapters may format it differently but must not weaken it.

### Role and background

You are a conservative transcription correction component for meetings about software system design. Speech recognition may confuse technical terms, split names, omit punctuation, repeat boundary words, or produce damaged grammar. Your task is to recover the most likely spoken wording using only supplied evidence. You are not an architect in this stage and must not improve, complete, or reinterpret the proposed design.

### Evidence priority

1. raw transcript and alternative hypotheses;
2. audio-derived confidence and timing metadata;
3. nearby meeting utterances;
4. confirmed entity names and aliases;
5. supplied technical glossary;
6. general software terminology.

A lower-priority source may disambiguate a higher-priority one, but may not introduce a new architectural claim.

### Permitted actions

- repair punctuation, capitalization, word boundaries, and obvious grammar damage;
- replace a likely phonetic error with a glossary term or confirmed nearby entity name;
- remove an exact duplicated boundary phrase caused by segmentation;
- retain spoken disfluencies only when they carry meaning; remove harmless fillers when configured;
- mark an unclear span rather than guessing;
- return the original text unchanged when evidence is insufficient.

### Prohibited actions

- adding services, databases, protocols, vendors, data flows, requirements, decisions, or reasons;
- changing sync to async, create to update, must to may, question to decision, or negation;
- resolving an architectural contradiction;
- replacing a generic term with a named technology merely because it is common;
- deleting uncertainty, disagreement, retraction, or correction language;
- rewriting the speaker into a “better design.”

### Required process

1. Identify damaged or suspicious spans.
2. Compare each span with alternatives, confidence, glossary, known names, and nearby context.
3. Make the smallest justified edit.
4. Re-check that actors, actions, targets, direction, modality, negation, protocol, and certainty retain the spoken meaning.
5. For every edit, emit original text, replacement, reason, evidence, and confidence.
6. If no safe improvement exists, leave the span unchanged and mark it unresolved.
7. Return only the specified structured result.

### Output constraints

- `corrected_text` must be understandable on its own but semantically equivalent to the evidence.
- `changed=false` when only the original is defensible.
- Confidence describes confidence in the correction, not confidence in the architecture.
- Never hide an unresolved phrase behind fluent invented prose.

## 8. Architecture agent — instruction contract v1

### Role and mission

You maintain a live, evidence-backed software architecture model from a meeting transcript. The meeting concerns software system design. Your job is to identify architecture facts, decisions, proposals, questions, corrections, and relationships; resolve them against the existing model; and propose the smallest valid mutation set. The deterministic validator, not you, commits changes.

### Inputs

- corrected utterance and immutable raw utterance;
- transcript IDs, speaker/timing/confidence metadata;
- recent conversation needed for references;
- current typed architecture graph with stable IDs and aliases;
- unresolved questions and contradictions;
- project policy and deterministic defaults;
- permitted entity, relationship, status, provenance, and mutation enums;
- AI Meeting Draw semantic capability summary (not raw canvas coordinates).

### Authority and evidence rules

1. Explicit spoken statements outrank defaults and prior tentative inferences.
2. Confirmed current facts remain until explicitly revised, rejected, or superseded.
3. Project-policy defaults may fill only fields named by the policy and must be tagged `defaulted`.
4. Common architectural knowledge may help classify a spoken item, but must not create extra components or requirements.
5. Every create/update/connect/confirm/retract mutation must cite transcript evidence.
6. An inference without adequate evidence becomes a question or no-op, not a fact.

### Permitted actions

- create a typed entity explicitly introduced by the speaker;
- classify “checkout service” as a service/microservice according to project policy;
- connect existing or newly created entities when the direction and intent are stated or safely defaultable;
- add generic infrastructure required by an explicit relationship only when project policy permits it;
- update aliases, descriptions, protocol, status, or confidence;
- merge clear duplicate mentions while preserving aliases and evidence;
- convert tentative/defaulted elements to confirmed when the speaker confirms them;
- raise a concise question for material ambiguity or contradiction;
- retract/supersede an item only from explicit correction or rejection language;
- return no-op when speech is non-architectural, repetitive, silent, or too uncertain.

### Prohibited actions

- inventing named technology, cloud vendor, database vendor, broker, authentication method, capacity, SLA, deployment topology, or security boundary;
- adding “best practice” components such as gateways, caches, queues, load balancers, observability, or retries unless stated or explicitly permitted by policy;
- treating a question as a decision or a suggestion as confirmed;
- creating a duplicate because wording or capitalization changed;
- reversing caller/callee or producer/consumer direction;
- silently resolving contradictions such as “synchronous using Kafka”;
- deleting confirmed objects because they were not mentioned recently;
- choosing pixels, coordinates, colors, or arbitrary AI Meeting Draw shapes;
- outputting narrative outside the strict mutation schema.

### Required reasoning workflow

1. **Relevance:** decide whether the utterance contains an architecture change, confirmation, retraction, question, or no-op.
2. **Claims:** extract explicit actors, actions, targets, qualifiers, protocols, modality, negation, certainty, and temporal/correction language.
3. **Identity:** search the current graph by stable ID, canonical name, aliases, type, recent references, and compatible context.
4. **Reference resolution:** resolve pronouns only with one strong antecedent. Otherwise raise a question.
5. **State comparison:** determine whether each claim creates, updates, confirms, contradicts, retracts, or repeats existing state.
6. **Defaults:** apply only the deterministic defaults supplied in the project policy, and tag each resulting field `defaulted`.
7. **Minimal patch:** propose the smallest set of typed, ordered mutations. Reuse stable IDs.
8. **Evidence:** attach supporting transcript IDs and a short evidence phrase to each mutation.
9. **Consistency:** check endpoint existence, direction, relationship compatibility, containment, duplicate identity, status, and contradiction.
10. **Uncertainty:** prefer a generic type, tentative state, question, or no-op over an unsupported precise claim.
11. **Output:** emit only the strict structured response.

### Relationship interpretation examples

- “Product service calls checkout service” -> identify/create both services; connect product to checkout; apply the configured unspecified-call default; mark protocol as defaulted.
- “Use gRPC for that call” -> update the existing relationship, do not create another parallel edge unless the speaker explicitly describes two call paths.
- “Orders are published to Kafka and fulfillment consumes them” -> create/reuse publisher, Kafka broker/topic representation, and consumer; create publish and consume relationships in the correct direction.
- “Should we add Redis?” -> create an open question/suggestion visualization only; do not create a confirmed Redis cache.
- “Yes, use Redis for product data” -> resolve the prior question and update the same tentative cache identity to confirmed.
- “Not Kafka—use a direct REST call” -> supersede/retract the Kafka relationship and create/update the direct synchronous relationship with linked evidence.
- “The backend handles it” with two possible backends -> raise a question; do not guess.

### Output contract

The result contains: `meeting_id`, `base_graph_version`, `input_transcript_ids`, `classification`, ordered `mutations`, `questions`, and `no_op_reason`. Each mutation contains operation, stable target/reference IDs, typed fields, provenance, confidence, evidence, and optional superseded mutation/entity/relationship ID. Empty mutations are valid.

## 9. Deterministic project defaults v1

Defaults are configuration, not hidden prompt lore. Each use is stored as `defaulted` and rendered as tentative until confirmed.

| Missing information | Default action | What must not be assumed | Impact |
|---|---|---|---|
| “A calls B” with no protocol | Synchronous REST over HTTPS, direct A -> B | Endpoint path, payload, auth, retries | Create a grey/dotted relationship labelled `REST/HTTPS (default)` |
| “backend service” in this project's microservice mode | Generic microservice | Framework, language, deployment unit | Use service/component identity; can be reclassified later |
| “service” outside microservice mode | Generic backend service | That it is independently deployed | Avoid overclaiming microservice boundaries |
| “stores data” with no store type | Generic database/storage, tentative | PostgreSQL or any vendor | Add generic cylinder only if a distinct store is implied |
| “caches data” | Generic cache | Redis/Memcached | Add generic cache with cache-read/write relation |
| Explicit asynchronous/event wording with no broker | Generic message channel/broker if policy permits | Kafka/RabbitMQ/topic name | Represent async semantics without inventing vendor |
| “Kafka” | Kafka broker/topic as context requires | Topic name, partitions, delivery guarantees | Use publish/consume edges; ask if topology is materially ambiguous |
| Explicit gRPC/GraphQL/WebSocket | Use the named protocol | Extra gateway or transport properties | Replace default protocol on the same relationship |
| “frontend calls backend” | Frontend -> backend | Browser/mobile subtype if unstated | Direct call, default protocol if absent |
| Named external provider/API | External system/third-party API | Ownership or internals | Place outside owned boundary |
| No ownership/domain stated | Leave ungrouped | Team, bounded context, deployment | Do not fabricate boundaries |
| No cloud/vendor stated | Remain vendor-neutral | AWS/GCP/Azure | Use generic visual, not cloud icon |
| Material contradictory wording | Raise open question | A convenient interpretation | No conflicting graph mutation until resolved |

Project-profile switches must include `DEFAULT_BACKEND_STYLE`, `DEFAULT_SYNC_PROTOCOL`, `ALLOW_GENERIC_ASYNC_INFRASTRUCTURE`, `DEFAULT_DATABASE_KIND`, and `DEFAULT_PROVENANCE_VISIBILITY`.

## 10. Architecture identity to AI Meeting Draw node mapping v1

| Architecture identity | Meaning and usage | AI Meeting Draw representation | Implications and defaults |
|---|---|---|---|
| Person / user / external actor | Human role interacting with the system; use for roles, not software | `actor` | Usually placed far left; outgoing interaction edges; never treat as deployable software |
| Client application | Generic caller when UI technology is unknown | `display` or `rounded` fallback | Left/client lane; may later specialize to browser/mobile |
| Browser/web frontend | User-facing web application | `display` | Left of services; owned boundary only if stated; default call to backend may be REST/HTTPS |
| Mobile application | User-facing mobile client | `display` with mobile icon when supported | Separate client identity; do not merge with web frontend without evidence |
| Backend service | Server-side software unit with unclear deployment granularity | `component` | Neutral service semantics; can later become microservice/monolith component |
| Microservice | Independently modeled service in microservice mode or explicitly named | `component` | Stable service boundary; does not imply database-per-service unless stated |
| API gateway | Entry point that routes/mediates API requests | `hexagon` | Place between clients and services; do not add automatically unless policy says so |
| Load balancer | Traffic distribution infrastructure | `trapezoid` or supported network icon | Indicates distribution, not an API contract; only explicit/default-policy creation |
| Database | Persistent queryable data store, vendor-neutral unless named | `cylinder` | Usually below owning/using service; relationship must say read/write when known |
| Cache | Temporary/accelerated data store | `internalStorage` | Does not imply system of record; connect with cache-read/write semantics |
| Object/file storage | Blob/document artifact storage | `storedData` | Use for objects/files, not general relational persistence |
| Queue | Point-to-point asynchronous buffer | `queue` | Separate producer/consumer direction; do not imply pub/sub |
| Topic / event stream | Publish-subscribe channel or named stream | `queue` with topic subtype metadata | Multiple consumers permitted; label topic when explicit |
| Message broker | Infrastructure hosting queues/topics | `component` or broker icon | May contain topic/queue children; vendor icon only when explicit |
| External system / SaaS | Software outside the modeled ownership boundary | `cloud` | Place far right/outside boundary; internals remain opaque |
| Third-party API | External callable interface | `cloud` or API icon | Relationship label includes intent/protocol when known |
| Domain/system boundary | Ownership, system, bounded context, or deployment grouping | `package` | Contains elements only when membership is evidenced; avoid decorative grouping |
| Unknown architecture component | Clearly referenced software whose type is unresolved | `rect` | Tentative; preserve identity and ask only if type matters |
| Architecture question | Unresolved material question tied to entities/edges | `callout` | Red/dashed; not part of confirmed topology; resolves into/update existing identity |
| Decision record | Explicitly accepted architectural decision | `diamond` | References affected objects; diamond is not used for services or routers |
| Suggestion / alternative | Proposed but unaccepted design option | `note` or `sticky` | Grey/dotted; must not appear confirmed |
| Risk / contradiction | Known concern or incompatible claims | `note` | Warning styling and evidence links; must not silently alter topology |

Named AWS/GCP/Azure icons may replace a generic shape only when the vendor/product is explicit or the project profile declares that platform. Semantic metadata remains independent of the icon.

## 11. Architecture relationship to AI Meeting Draw edge mapping v1

Every inter-process edge must have direction and an intent label. Protocol/technology is added when explicit or defaulted.

| Relationship | Meaning | Default edge | Important implications |
|---|---|---|---|
| Synchronous call | Caller waits for response | Solid arrow, elbow routing, label `intent — protocol` | Direction caller -> callee; default protocol is visibly marked if inferred |
| Async publish | Producer emits to channel/broker | Dashed arrow producer -> channel | Does not mean a particular consumer receives exactly once |
| Async consume | Channel delivers/read by consumer | Dashed arrow channel -> consumer | Keep separate from publish so direction is unambiguous |
| Read | Component retrieves data | Solid arrow component -> store labelled `read` | Does not imply ownership |
| Write | Component persists/modifies data | Solid arrow component -> store labelled `write` | May coexist with read; combine label only if UI clarity improves |
| Cache access | Component accesses cache | Solid/dashed neutral arrow labelled `cache read/write` | Cache is not source of truth unless stated |
| Authentication/authorization | Component delegates identity/access decision | Arrow labelled `auth` plus named protocol if explicit | Do not infer OAuth/JWT |
| File/object transfer | Component reads/writes artifact | Arrow labelled with transfer intent/protocol | Distinct from database query |
| Dependency | Build/runtime/logical dependency without request flow | Open/triangle or basic arrow labelled `depends on` | Must not be mistaken for runtime network traffic |
| Containment | Element belongs to boundary/broker/package | Parent-child grouping, not a traffic edge | Membership/ownership only |
| Replication | Data copies between stores | Directed/double edge labelled `replication` | Direction and active/passive semantics only if stated |
| Unknown connection | A relationship exists but semantics are unclear | Dotted neutral arrow labelled `relationship unclear` | Prefer a question before choosing protocol |

### Provenance and status styling

| State | Visual convention | Transition behavior |
|---|---|---|
| Explicit/confirmed | Normal fill and solid edge | Stable; update same AI Meeting Draw object on later detail |
| Defaulted/inferred | Muted grey, dotted edge, `(default)` label | Convert in place when confirmed or overridden |
| Suggested | Grey fill/outline and dotted treatment | Preserve as option; never merge into confirmed without evidence |
| Questioned | Red outline/callout and dashed association | Resolve/update in place; retain decision history |
| Contradicted | Warning color plus linked note | Freeze unsafe mutation until resolved |
| Retracted/superseded | Removed from active view or shown faded in history mode | Keep database history and evidence |

## 12. Provider and orchestration policy

- `INTELLIGENCE_PROVIDER` selects Gemini, Groq, or Qwen; all use the same prompt version, schema, defaults, validator, and fixtures.
- Transcript correction may use a separate `CORRECTION_PROVIDER`; it defaults to the intelligence provider but can be disabled or routed to a cheaper/local model.
- Timeouts, maximum repair attempts, temperature, maximum tokens, and concurrency are configurable per stage.
- Architecture temperature should be low. Strict structured output is required where supported.
- A provider response is a proposal. Only validated, version-matched mutations commit.
- On quota/timeout/provider failure, persist the pending job and continue recording/STT. Retry in order or allow manual replay.
- Secrets never appear in prompts, logs, browser events, SQLite payloads, or exports.

## 13. Testing matrix

Each phase adds unit, integration, replay, and provider-contract tests. Required end-to-end scenarios include:

1. create two services and a default REST connection;
2. override REST with gRPC without duplicating the edge;
3. publish through Kafka and consume from another service;
4. distinguish queue from pub/sub;
5. ask about Redis, then confirm it, preserving identity;
6. retract Kafka and replace it with direct REST;
7. resolve “it” when unambiguous and ask when ambiguous;
8. correct “Cafka” to Kafka but leave an unknown product name unresolved;
9. ignore silence/noise and non-architectural conversation;
10. prevent a model from inventing PostgreSQL, AWS, authentication, or a gateway;
11. recover after provider failure without transcript loss or duplicate mutation;
12. preserve manual AI Meeting Draw positions while adding new nodes;
13. handle out-of-order intelligence responses through rebase/revalidation;
14. export raw and corrected transcript with timestamps and the final graph/audit log.

## 14. Phase execution protocol

For every phase, perform these steps in order:

1. restate phase scope and list files expected to change;
2. inspect current implementation and record compatibility constraints;
3. add or update schema/configuration with safe defaults;
4. implement the smallest complete vertical slice;
5. add unit tests before broad integration;
6. run existing and new tests;
7. run relevant Phase 0 fixtures and compare metrics;
8. document configuration, migration, operation, and rollback;
9. publish a phase completion report containing changed files, test evidence, metric movement, known limits, and exact exit-gate result;
10. stop. Begin the next phase only after the current phase is accepted.

If a phase reveals that a later assumption is wrong, update this plan explicitly with a versioned decision record; do not silently skip or reorder work.

## 15. Recommended implementation order and dependencies

```text
Phase 0 baseline
   -> Phase 1 audio
   -> Phase 2 STT contract
   -> Phase 3 correction
   -> Phase 4 typed graph/storage
   -> Phase 5 deterministic rules/validator
   -> Phase 6 architecture agent
   -> Phase 7 AI Meeting Draw mapping
   -> Phase 8 layout/rendering
   -> Phase 9 lifecycle/UX/recovery
   -> Phase 10 evaluation/release
```

Phase 7 mapping can be prototyped with static fixtures after Phase 4, but it must not replace or bypass Phases 5–6 in the integrated pipeline.

## 16. Research basis

- WhisperX uses VAD-based segmentation and batching to improve long-form transcription performance: <https://arxiv.org/abs/2303.00747>
- Whisper/faster-whisper expose prompt, hotword, previous-text, no-speech, and probability controls useful for domain adaptation and quality gates: <https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py>
- WhisperX exposes initial prompt, hotwords, and VAD controls: <https://github.com/m-bain/whisperX/blob/main/whisperx/transcribe.py>
- ASR error-correction research supports using hypotheses/context but also shows that unconstrained prompt-only correction can introduce errors, which motivates conditional correction, immutable raw text, and strict evaluation: <https://arxiv.org/abs/2309.15701> and <https://aclanthology.org/2025.emnlp-industry.110.pdf>
- C4 distinguishes people, software systems, containers, components, and code, and recommends directional, intent-labelled relationships with technology/protocol on inter-process connections: <https://c4model.com/abstractions> and <https://c4model.com/diagrams/notation>

## 17. Definition of complete

The milestone is complete only when a meeting can be replayed from stored evidence to produce the same validated architecture graph; raw and corrected transcripts remain distinguishable; defaults and uncertainty remain visible; entities and edges update rather than duplicate; unsupported facts are rejected; AI Meeting Draw uses meaningful node and edge types with stable layout; failures do not lose audio/transcripts; and the evaluation corpus demonstrates an improvement over the Phase 0 baseline.
