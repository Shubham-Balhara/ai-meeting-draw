from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import uuid

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from app.api.collab import push_to_room
from app.api.schemas import BLANK_BOARD_SVG
from app.services.diagram_render import diagram_to_svg

from .config import MeetingSettings
from .correction import CorrectionCoordinator, CorrectionProvider, CorrectionRequest
from .architecture import ArchitectureGraph
from .architecture_agent import (AgentTranscript, ArchitectureAgentProvider, ArchitectureCoordinator,
                                 StatefulArchitectureAgent)
from .architecture_diagram import ArchitectureDiagramMapper
from .diagram import DiagramAdapter, DiagramUpdateService, MemoryBindings
from .export import export_transcript
from .intelligence import IntelligenceCoordinator
from .observability import CORRECTION_PROMPT_VERSION
from .layout import IncrementalArchitectureLayout
from .policy import ArchitecturePolicy, apply_defaults
from .architecture import EntityType
from .providers import ProviderConfig, create_provider
from .schema import AnalysisRequest, AnalysisResult, TranscriptSegment
from .storage import MeetingStore
from .firestore_mirror import FirestoreMirror
from .stt import create_transcriber
from .transcription import TranscriptionPipeline


logger = logging.getLogger("ai_meeting_draw.meeting")
router = APIRouter(tags=["meetings"])


def configure_meetings(app) -> None:
    settings = MeetingSettings()
    app.state.meeting_settings = settings
    app.state.meeting_store = MeetingStore(
        settings.database_path,
        firestore=FirestoreMirror(
            enabled=settings.firestore_enabled,
            required=settings.firestore_required,
            project_id=settings.firestore_project_id,
            database=settings.firestore_database,
            collection=settings.firestore_collection,
        ),
    )

    @app.on_event("startup")
    async def _meeting_startup() -> None:
        await app.state.meeting_store.open()
        logger.info(
            "Meeting subsystem ready database=%s stt=%s intelligence=%s/%s architecture_v2=%s mapper_v2=%s correction=%s",
            settings.database_path,
            settings.stt_provider,
            settings.intelligence_provider if settings.intelligence_enabled else "disabled",
            settings.intelligence_model if settings.intelligence_enabled else "disabled",
            settings.enable_architecture_agent_v2,
            settings.enable_diagram_mapper_v2,
            settings.enable_transcript_correction,
        )

    @app.on_event("shutdown")
    async def _meeting_shutdown() -> None:
        await app.state.meeting_store.close()
        logger.info("Meeting subsystem stopped")


async def _safe_send(websocket: WebSocket, event: dict) -> None:
    try:
        await websocket.send_json(event)
    except Exception:
        logger.debug("Unable to send WebSocket event type=%s", event.get("type"), exc_info=True)


def _provider_config(settings: MeetingSettings) -> ProviderConfig:
    base = settings.intelligence_base_url
    if settings.intelligence_provider == "grok":
        base = base or "https://api.x.ai/v1"
    elif settings.intelligence_provider == "groq":
        base = base or "https://api.groq.com/openai/v1"
    elif settings.intelligence_provider == "qwen":
        base = base or "http://127.0.0.1:11434"
    return ProviderConfig(
        provider=settings.intelligence_provider,
        model=settings.intelligence_model,
        api_key=settings.intelligence_api_key,
        base_url=base,
        timeout_seconds=settings.intelligence_timeout_seconds,
        retry_attempts=settings.intelligence_retry_attempts,
        ollama_num_ctx=settings.ollama_num_ctx,
        ollama_think=settings.ollama_think,
        gemini_backend=settings.gemini_backend,
        google_cloud_project=settings.firestore_project_id,
        google_cloud_location=settings.google_cloud_location,
    )


def _correction_provider_config(settings: MeetingSettings) -> ProviderConfig:
    base = settings.intelligence_base_url
    if settings.correction_provider == "grok": base = base or "https://api.x.ai/v1"
    elif settings.correction_provider == "groq": base = base or "https://api.groq.com/openai/v1"
    elif settings.correction_provider == "qwen": base = base or "http://127.0.0.1:11434"
    return ProviderConfig(provider=settings.correction_provider, model=settings.correction_model,
        api_key=settings.intelligence_api_key, base_url=base,
        timeout_seconds=settings.intelligence_timeout_seconds,
        retry_attempts=settings.intelligence_retry_attempts,
        ollama_num_ctx=settings.ollama_num_ctx,
        ollama_think=settings.ollama_think,
        gemini_backend=settings.gemini_backend,
        google_cloud_project=settings.firestore_project_id,
        google_cloud_location=settings.google_cloud_location)


def _semantic_operations(result: AnalysisResult) -> list[dict]:
    operations: list[dict] = []
    categories = {
        "proposal": "suggestion", "question": "question", "decision": "decision",
        "action_item": "action_item", "risk": "risk", "blocker": "risk",
        "topic": "fact", "assumption": "fact", "note": "fact",
    }
    for item in result.insights:
        data = item.model_dump(mode="json")
        data.update(op="upsert_node", category=categories.get(item.kind.value, "fact"))
        operations.append(data)
        if item.action == "supersede" and item.supersedes_semantic_key:
            operations.append({"op": "mark_superseded", "semantic_key": item.supersedes_semantic_key})
    for item in result.links:
        data = item.model_dump(mode="json")
        data.update(
            op="remove" if item.action == "remove" else "upsert_edge",
            object_kind="edge",
            source_semantic_key=item.source_key,
            target_semantic_key=item.target_key,
        )
        operations.append(data)
    return operations


async def _new_board(document_service, external_id: str) -> str:
    diagram = {"pages": [{"id": "meeting", "name": "Meeting Intelligence", "nodes": [], "edges": []}]}
    meta = await asyncio.to_thread(
        document_service.create,
        diagram_to_svg(diagram),
        f"Meeting — {external_id[:80]}",
        diagram,
    )
    return meta.id


def _diagram_service(document_service, bindings: MemoryBindings) -> DiagramUpdateService:
    async def load(board_id: str):
        doc = await asyncio.to_thread(document_service.get, board_id)
        return {"diagram": doc.diagram}

    async def save(board_id: str, diagram: dict):
        svg = diagram_to_svg(diagram)
        await asyncio.to_thread(document_service.save, board_id, svg, diagram, "Meeting Intelligence")

    return DiagramUpdateService(
        DiagramAdapter(bindings), load=load, save=save, broadcast=push_to_room
    )


@router.get("/api/meetings/health")
async def meeting_health(request: Request) -> dict:
    settings: MeetingSettings = request.app.state.meeting_settings
    return {
        "status": "ok",
        "stt_provider": settings.stt_provider,
        "gemini_backend": settings.gemini_backend if settings.stt_provider == "gemini" else None,
        "google_cloud_location": settings.google_cloud_location if settings.gemini_backend == "vertex" else None,
        "intelligence_enabled": settings.intelligence_enabled,
        "intelligence_provider": settings.intelligence_provider if settings.intelligence_enabled else None,
        "intelligence_model": settings.intelligence_model if settings.intelligence_enabled else None,
        "database": str(settings.database_path),
        "firestore_enabled": settings.firestore_enabled,
        "firestore_required": settings.firestore_required,
    }


@router.get("/api/meetings/{session_id}")
async def meeting_detail(request: Request, session_id: str) -> dict:
    store: MeetingStore = request.app.state.meeting_store
    meeting = await store.meeting(session_id)
    if meeting is None:
        return {"status": "not_found"}
    return {"meeting": meeting, "transcripts": await store.transcript_segments(session_id)}


@router.websocket("/ws/meetings/{external_id}/audio")
@router.websocket("/v1/meetings/{external_id}/audio")
async def meeting_audio(websocket: WebSocket, external_id: str) -> None:
    await websocket.accept()
    logger.info("Meeting WebSocket accepted external_id=%s", external_id)
    app = websocket.app
    settings: MeetingSettings = app.state.meeting_settings
    store: MeetingStore = app.state.meeting_store
    document_service = app.state.document_service
    session_id = recording_id = board_id = None
    run_id = correlation_id = None
    pipeline: TranscriptionPipeline | None = None
    intelligence: IntelligenceCoordinator | None = None
    correction: CorrectionCoordinator | None = None
    architecture: ArchitectureCoordinator | None = None
    audio_file = None
    chunks = bytes_received = operation_ordinal = 0
    started = datetime.now(timezone.utc)

    try:
        settings.validate()
        start = await websocket.receive_json()
        if start.get("type") != "start":
            logger.warning("Rejected meeting connection without start event external_id=%s", external_id)
            await websocket.close(code=1008, reason="Expected start message")
            return

        board_id = await _new_board(document_service, external_id)
        session_id = await store.create_meeting(
            external_id,
            client_metadata={**start, "board_id": board_id, "board_url": f"/d/{board_id}"},
            intelligence_provider=settings.intelligence_provider if settings.intelligence_enabled else None,
            intelligence_model=settings.intelligence_model if settings.intelligence_enabled else None,
        )
        recording_id = str(uuid.uuid4())
        settings.recordings_dir.mkdir(parents=True, exist_ok=True)
        recording_path = settings.recordings_dir / f"{recording_id}.webm"
        transcriber = create_transcriber(settings)
        await store.create_recording(
            session_id, str(recording_path), transcriber.name,
            recording_id=recording_id, stt_model=transcriber.model,
        )
        run_id, correlation_id = str(uuid.uuid4()), str(uuid.uuid4())
        await store.start_pipeline_run(meeting_id=session_id, recording_id=recording_id,
            run_id=run_id, correlation_id=correlation_id, stt_provider=transcriber.name,
            stt_model=transcriber.model,
            intelligence_provider=settings.intelligence_provider if settings.intelligence_enabled else None,
            intelligence_model=settings.intelligence_model if settings.intelligence_enabled else None)
        await store.observe_pipeline_event(run_id=run_id,meeting_id=session_id,recording_id=recording_id,
            event_name="meeting.started",stage="meeting",status="succeeded")
        logger.info(
            "Meeting session started session_id=%s recording_id=%s board_id=%s",
            session_id,
            recording_id,
            board_id,
        )
        bindings = MemoryBindings()
        diagram_service = _diagram_service(document_service, bindings)

        async def on_intelligence(result: AnalysisResult, request: AnalysisRequest) -> None:
            nonlocal operation_ordinal
            logger.info(
                "Intelligence result session_id=%s insights=%d links=%d sequences=%s",
                session_id,
                len(result.insights),
                len(result.links),
                request.new_sequence_ids,
            )
            insight_ids: dict[str, str] = {}
            for item in result.insights:
                insight_id = str(uuid.uuid5(uuid.UUID(session_id), item.semantic_key))
                stored_id, revision = await store.upsert_insight(
                    insight_id=insight_id, meeting_id=session_id, dedupe_key=item.semantic_key,
                    kind=item.kind.value, status=item.status.value, text=item.text, owner=item.owner,
                    due_at=item.due_date.isoformat() if item.due_date else None,
                    confidence=item.confidence, source_start_sequence=min(item.evidence_sequences),
                    source_end_sequence=max(item.evidence_sequences), provider=settings.intelligence_provider,
                    model=settings.intelligence_model,
                )
                insight_ids[item.semantic_key] = stored_id
                operation_id = str(uuid.uuid5(uuid.UUID(session_id), f"{item.semantic_key}:{revision}"))
                await store.enqueue_diagram_operation(
                    operation_id=operation_id, meeting_id=session_id, ordinal=operation_ordinal,
                    operation_type="create" if revision == 1 else "update", request=item.model_dump(mode="json"),
                    insight_id=stored_id,
                )
                operation_ordinal += 1
            for item in result.links:
                operation_id = str(uuid.uuid5(
                    uuid.UUID(session_id),
                    f"link:{item.semantic_key}:{operation_ordinal}",
                ))
                await store.enqueue_diagram_operation(
                    operation_id=operation_id,
                    meeting_id=session_id,
                    ordinal=operation_ordinal,
                    operation_type="delete" if item.action == "remove" else "update",
                    request=item.model_dump(mode="json"),
                )
                operation_ordinal += 1

            operations = _semantic_operations(result)
            try:
                updated = await diagram_service.apply(board_id, operations, page_id="meeting")
                logger.info(
                    "Diagram updated session_id=%s board_id=%s operations=%d",
                    session_id,
                    board_id,
                    len(operations),
                )
                for key, insight_id in insight_ids.items():
                    element_id = bindings.get("node", key)
                    if element_id:
                        await store.bind_diagram_element(meeting_id=session_id, insight_id=insight_id,
                                                         element_id=element_id, element_type="node")
                pending = await store.fetch_all(
                    "SELECT id FROM diagram_operations WHERE meeting_id=? AND status='pending' ORDER BY ordinal",
                    (session_id,),
                )
                for row in pending:
                    await store.complete_diagram_operation(row["id"], response={"board_id": board_id})
                await store.set_checkpoint(session_id, "intelligence", max(request.new_sequence_ids))
                await _safe_send(websocket, {
                    "type": "meeting_insights", "provider": settings.intelligence_provider,
                    "model": settings.intelligence_model, "result": result.model_dump(mode="json"),
                    "board_id": board_id, "board_url": f"/d/{board_id}",
                })
            except Exception as exc:
                logger.exception("Diagram update failed session_id=%s board_id=%s", session_id, board_id)
                await store.add_error(stage="diagram", message=str(exc), meeting_id=session_id)
                raise

        async def on_intelligence_error(exc: Exception, request: AnalysisRequest | None) -> None:
            logger.exception("Intelligence processing failed session_id=%s", session_id, exc_info=exc)
            await store.add_error(stage="intelligence", message=str(exc), meeting_id=session_id,
                                  provider=settings.intelligence_provider)
            await _safe_send(websocket, {"type":"intelligence_error", "message":str(exc)})

        async def on_architecture(result, before: ArchitectureGraph, after: ArchitectureGraph) -> None:
            logger.info("Applying architecture result session_id=%s classification=%s base_version=%d resulting_version=%d mutations=%d",
                        session_id,result.classification,before.version,after.version,len(result.mutations))
            current = before
            for item in result.mutations:
                mutation = apply_defaults(item, architecture.agent.policy)
                next_graph = current.apply(mutation)
                mutation_id = str(uuid.uuid5(uuid.UUID(session_id), f"architecture:{next_graph.version}:{mutation.target_id}"))
                await store.commit_architecture_mutation(
                    meeting_id=session_id, mutation_id=mutation_id, operation=mutation.operation,
                    target_id=mutation.target_id, mutation_payload=mutation.model_dump(mode="json"),
                    resulting_graph=next_graph, provenance=mutation.provenance.value,
                    confidence=mutation.confidence,
                    evidence=[e.model_dump(mode="json") for e in mutation.evidence],
                    base_version=current.version,
                )
                logger.debug("Architecture mutation persisted session_id=%s mutation_id=%s operation=%s target_id=%s graph_version=%d",
                             session_id,mutation_id,mutation.operation,mutation.target_id,next_graph.version)
                current = next_graph
            if current != after:
                raise ValueError("persisted architecture graph differs from validated graph")
            doc = await asyncio.to_thread(document_service.get, board_id)
            diagram = doc.diagram or {"pages":[{"id":"meeting","name":"Meeting Intelligence","nodes":[],"edges":[]}]}
            page = next((p for p in diagram.get("pages",[]) if p.get("id")=="meeting"), None)
            if page is None: raise ValueError("meeting diagram page is missing")
            existing_ids = {node["id"] for node in page.get("nodes",[])}
            logger.debug("Preparing architecture render session_id=%s board_id=%s existing_nodes=%d existing_edges=%d",
                         session_id,board_id,len(page.get("nodes",[])),len(page.get("edges",[])))
            rendered = ArchitectureDiagramMapper().render(after, page)
            rendered = IncrementalArchitectureLayout().apply(rendered, existing_node_ids=existing_ids)
            page.update(rendered)
            await asyncio.to_thread(document_service.save, board_id, diagram_to_svg(diagram), diagram, "Meeting Intelligence")
            await push_to_room(board_id,{"t":"state","diagram":rendered,"from":0,"pageId":"meeting",
                                         "removedNodeIds":[],"removedEdgeIds":[]})
            logger.info("Architecture diagram saved session_id=%s board_id=%s graph_version=%d nodes=%d edges=%d",
                        session_id,board_id,after.version,len(rendered["nodes"]),len(rendered["edges"]))
            await _safe_send(websocket,{"type":"architecture_updated","graph_version":after.version,
                "classification":result.classification,"mutations":[m.model_dump(mode="json") for m in result.mutations],
                "board_id":board_id,"board_url":f"/d/{board_id}"})

        async def on_architecture_error(exc: Exception, item: AgentTranscript) -> None:
            logger.exception("Architecture processing failed session_id=%s transcript_id=%s",session_id,item.transcript_id,exc_info=exc)
            await store.add_error(stage="intelligence",message=str(exc),meeting_id=session_id,provider=settings.intelligence_provider)
            await _safe_send(websocket,{"type":"architecture_error","sequence":item.sequence,"message":str(exc)})

        if settings.intelligence_enabled and settings.enable_architecture_agent_v2:
            initial_graph = ArchitectureGraph.model_validate(await store.architecture_graph(session_id))
            architecture_policy = ArchitecturePolicy(
                default_backend_style=EntityType(settings.architecture_default_backend_style),
                default_sync_protocol=settings.architecture_default_sync_protocol,
                allow_generic_async_infrastructure=settings.allow_generic_async_infrastructure,
                default_provenance_visibility=settings.default_provenance_visibility,
            )
            architecture = ArchitectureCoordinator(session_id,
                StatefulArchitectureAgent(ArchitectureAgentProvider(_provider_config(settings)), architecture_policy),
                initial_graph,on_architecture,on_error=on_architecture_error,
                batch_seconds=settings.intelligence_batch_seconds)
            await architecture.start()
            logger.info("Architecture agent v2 started session_id=%s provider=%s model=%s graph_version=%d",
                        session_id,settings.intelligence_provider,settings.intelligence_model,initial_graph.version)
        elif settings.intelligence_enabled:
            intelligence = IntelligenceCoordinator(
                session_id, create_provider(_provider_config(settings)), on_intelligence,
                on_error=on_intelligence_error, batch_seconds=settings.intelligence_batch_seconds,
                min_new_chars=settings.intelligence_min_new_chars,
                context_segments=settings.intelligence_context_segments,
            )
            await intelligence.start()
            logger.info("Intelligence coordinator started session_id=%s", session_id)
        else:
            logger.info("Meeting intelligence disabled session_id=%s; transcripts will not update the diagram",session_id)

        async def submit_architecture(transcript_id:int, sequence:int, start_seconds:float, end_seconds:float, text:str) -> None:
            if architecture and text.strip():
                logger.debug("Submitting transcript to architecture session_id=%s transcript_id=%d sequence=%d characters=%d",
                             session_id,transcript_id,sequence,len(text))
                await architecture.submit(AgentTranscript(transcript_id=transcript_id,sequence=sequence,text=text,
                    start_seconds=start_seconds,end_seconds=end_seconds))

        async def on_correction(request: CorrectionRequest, result) -> None:
            correction_id = str(uuid.uuid5(uuid.UUID(session_id), f"correction:{request.transcript_id}:{CORRECTION_PROMPT_VERSION}"))
            await store.add_transcript_correction(
                correction_id=correction_id, transcript_id=request.transcript_id, meeting_id=session_id,
                raw_text=request.raw_text, corrected_text=result.corrected_text, changed=result.changed,
                edits=[item.model_dump(mode="json") for item in result.edits], unresolved=result.unresolved_spans,
                confidence=result.confidence, provider=settings.correction_provider,
                model=settings.correction_model, prompt_version=CORRECTION_PROMPT_VERSION,
            )
            await _safe_send(websocket, {"type":"transcript_corrected","sequence":request.sequence,
                "raw_text":request.raw_text,"corrected_text":result.corrected_text,"changed":result.changed,
                "confidence":result.confidence,"unresolved_spans":result.unresolved_spans})
            if intelligence and result.corrected_text.strip():
                await intelligence.submit(TranscriptSegment(sequence=request.sequence,
                    start_seconds=request.start_seconds, end_seconds=request.end_seconds, text=result.corrected_text))
            await submit_architecture(request.transcript_id,request.sequence,request.start_seconds,request.end_seconds,result.corrected_text)

        if settings.enable_transcript_correction:
            correction = CorrectionCoordinator(
                CorrectionProvider(_correction_provider_config(settings)), on_correction,
                confidence_threshold=settings.correction_confidence_threshold,
                queue_size=settings.correction_queue_size,
            )
            await correction.start()
            logger.info("Transcript correction started session_id=%s provider=%s model=%s",
                        session_id, settings.correction_provider, settings.correction_model)

        async def ordered_sink(event: dict) -> None:
            if event["type"] == "transcript":
                await store.add_transcript(
                    meeting_id=session_id, recording_id=recording_id, sequence=event["sequence"],
                    start_ms=round(event["start_seconds"]*1000), end_ms=round(event["end_seconds"]*1000),
                    text=event["text"], provider=event["provider"], model=event.get("model"),
                    language=event.get("language"), confidence=event.get("confidence"),
                    raw_response={"average_log_probability":event.get("average_log_probability"),
                                  "no_speech_probability":event.get("no_speech_probability"),
                                  "metadata":event.get("stt_metadata") or {}},
                )
                await store.increment_run_counters(run_id,segments_seen=1,transcripts_stored=1)
                await store.observe_pipeline_event(run_id=run_id,meeting_id=session_id,recording_id=recording_id,
                    event_name="transcript.persisted",stage="storage",status="succeeded",
                    sequence=event["sequence"],provider=event["provider"],model=event.get("model"))
                row = await store.transcript_by_sequence(recording_id, event["sequence"])
                if correction and event["text"].strip():
                    await correction.submit(CorrectionRequest(
                        meeting_id=session_id, transcript_id=row["id"], sequence=event["sequence"],
                        start_seconds=event["start_seconds"], end_seconds=event["end_seconds"],
                        raw_text=event["text"], stt_confidence=event.get("confidence"),
                        average_log_probability=event.get("average_log_probability"),
                        no_speech_probability=event.get("no_speech_probability"),
                        glossary=[item.strip() for item in settings.stt_hotwords.split(",") if item.strip()],
                    ))
                elif intelligence and event["text"].strip():
                    await intelligence.submit(TranscriptSegment(
                        sequence=event["sequence"], start_seconds=event["start_seconds"],
                        end_seconds=event["end_seconds"], text=event["text"],
                    ))
                elif architecture and event["text"].strip():
                    await submit_architecture(row["id"],event["sequence"],event["start_seconds"],event["end_seconds"],event["text"])
                logger.debug("Transcript persisted session_id=%s sequence=%s", session_id, event["sequence"])
            else:
                logger.error(
                    "STT error session_id=%s sequence=%s message=%s",
                    session_id,
                    event.get("sequence"),
                    event.get("message"),
                )
                await store.add_error(stage="stt", message=event["message"], meeting_id=session_id,
                                      recording_id=recording_id, sequence=event.get("sequence"), provider=event.get("provider"))
            await _safe_send(websocket, event)

        pipeline = TranscriptionPipeline(transcriber, settings, ordered_sink)
        await pipeline.start()
        audio_file = recording_path.open("wb")
        await websocket.send_json({
            "type":"ready", "meeting_id":external_id, "session_id":session_id,
            "recording_id":recording_id, "stt_provider":transcriber.name,
            "intelligence_provider":settings.intelligence_provider if (intelligence or architecture) else None,
            "architecture_agent_v2":bool(architecture),
            "board_id":board_id, "board_url":f"/d/{board_id}",
        })
        logger.info("Meeting client ready session_id=%s", session_id)

        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect": raise WebSocketDisconnect()
            if message.get("bytes") is not None:
                chunk=message["bytes"]; audio_file.write(chunk); await pipeline.write(chunk)
                chunks += 1; bytes_received += len(chunk)
                logger.debug(
                    "Audio chunk received session_id=%s chunk=%d chunk_bytes=%d total_bytes=%d",
                    session_id,
                    chunks,
                    len(chunk),
                    bytes_received,
                )
                if chunks % 5 == 0:
                    await store.update_recording_progress(recording_id, bytes_received=bytes_received, chunks_received=chunks)
            elif message.get("text"):
                if json.loads(message["text"]).get("type") == "stop":
                    logger.info("Meeting stop requested session_id=%s", session_id)
                    break

        audio_file.close(); audio_file=None
        await pipeline.finish()
        if correction: await correction.finish()
        if architecture: await architecture.finish()
        if intelligence: await intelligence.finish()
        duration=(datetime.now(timezone.utc)-started).total_seconds()
        digest=await asyncio.to_thread(lambda: hashlib.sha256(recording_path.read_bytes()).hexdigest())
        await store.finish_recording(recording_id, duration_ms=round(duration*1000), bytes_received=bytes_received,
                                     chunks_received=chunks, sha256=digest)
        await store.finish_meeting(session_id)
        artifacts=await export_transcript(store, session_id, settings.transcript_export_dir)
        await store.observe_pipeline_event(run_id=run_id,meeting_id=session_id,recording_id=recording_id,
            event_name="meeting.completed",stage="meeting",status="succeeded")
        await store.finish_pipeline_run(run_id,"completed")
        await websocket.send_json({"type":"completed","session_id":session_id,"recording_id":recording_id,
                                   "board_id":board_id,"board_url":f"/d/{board_id}","chunks_received":chunks,
                                   "bytes_received":bytes_received,"duration_seconds":round(duration,2),
                                   "transcript_files":{k:str(v) for k,v in artifacts.items()}})
        logger.info(
            "Meeting completed session_id=%s duration_seconds=%.2f chunks=%d bytes=%d",
            session_id,
            duration,
            chunks,
            bytes_received,
        )
    except WebSocketDisconnect:
        logger.info("Meeting client disconnected session_id=%s", session_id)
        if pipeline: await pipeline.abort()
        if correction: await correction.abort()
        if architecture: await architecture.abort()
        if intelligence: await intelligence.abort()
        if recording_id: await store.finish_recording(recording_id,status="disconnected",bytes_received=bytes_received,chunks_received=chunks)
        if session_id: await store.finish_meeting(session_id,status="disconnected")
        if run_id:
            await store.observe_pipeline_event(run_id=run_id,meeting_id=session_id,recording_id=recording_id,
                event_name="meeting.disconnected",stage="meeting",status="cancelled")
            await store.finish_pipeline_run(run_id,"disconnected")
    except Exception as exc:
        logger.exception("Meeting session failed")
        if pipeline: await pipeline.abort()
        if correction: await correction.abort()
        if architecture: await architecture.abort()
        if intelligence: await intelligence.abort()
        if session_id: await store.add_error(stage="meeting",message=str(exc),meeting_id=session_id,recording_id=recording_id)
        if recording_id: await store.finish_recording(recording_id,status="failed",bytes_received=bytes_received,chunks_received=chunks)
        if session_id: await store.finish_meeting(session_id,status="failed")
        if run_id:
            await store.observe_pipeline_event(run_id=run_id,meeting_id=session_id,recording_id=recording_id,
                event_name="meeting.failed",stage="meeting",status="failed",error_code=type(exc).__name__)
            await store.finish_pipeline_run(run_id,"failed")
        await _safe_send(websocket,{"type":"error","message":str(exc)})
        try: await websocket.close(code=1011)
        except Exception: pass
    finally:
        if audio_file: audio_file.close()
