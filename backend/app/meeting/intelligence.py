"""Sequential per-meeting batching and state coordination."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
import contextlib
import logging

from .providers import IntelligenceProvider
from .schema import AnalysisRequest, AnalysisResult, InsightState, LinkState, TranscriptSegment


ResultCallback = Callable[[AnalysisResult, AnalysisRequest], Awaitable[None]]
ErrorCallback = Callable[[Exception, AnalysisRequest | None], Awaitable[None]]
_STOP = object()
logger = logging.getLogger("ai_meeting_draw.meeting.intelligence")


class IntelligenceCoordinator:
    """Batches ordered transcripts while guaranteeing one model call at a time."""

    def __init__(
        self,
        meeting_id: str,
        provider: IntelligenceProvider,
        on_result: ResultCallback,
        *,
        on_error: ErrorCallback | None = None,
        batch_seconds: float = 20.0,
        min_new_chars: int = 120,
        context_segments: int = 30,
        queue_size: int = 64,
    ):
        if batch_seconds <= 0 or min_new_chars < 1 or context_segments < 1 or queue_size < 1:
            raise ValueError("coordinator limits must be positive")
        self.meeting_id = meeting_id
        self.provider = provider
        self.on_result = on_result
        self.on_error = on_error
        self.batch_seconds = batch_seconds
        self.min_new_chars = min_new_chars
        self.context: deque[TranscriptSegment] = deque(maxlen=context_segments)
        self.queue: asyncio.Queue[TranscriptSegment | object] = asyncio.Queue(queue_size)
        self.insights: dict[str, InsightState] = {}
        self.links: dict[str, LinkState] = {}
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"intelligence:{self.meeting_id}")
            logger.info("Intelligence coordinator running meeting_id=%s", self.meeting_id)

    async def submit(self, segment: TranscriptSegment) -> None:
        if self._closed:
            raise RuntimeError("intelligence coordinator is closed")
        await self.start()
        await self.queue.put(segment)
        logger.debug(
            "Queued transcript for intelligence meeting_id=%s sequence=%d characters=%d queue=%d",
            self.meeting_id,
            segment.sequence,
            len(segment.text),
            self.queue.qsize(),
        )

    async def finish(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._task is None:
            await self.provider.close()
            return
        await self.queue.put(_STOP)
        await self._task
        await self.provider.close()
        logger.info("Intelligence coordinator completed meeting_id=%s", self.meeting_id)

    async def abort(self) -> None:
        self._closed = True
        logger.warning("Aborting intelligence coordinator meeting_id=%s", self.meeting_id)
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.provider.close()

    async def _run(self) -> None:
        pending: list[TranscriptSegment] = []
        while True:
            try:
                item = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=self.batch_seconds if pending else None,
                )
            except asyncio.TimeoutError:
                logger.debug("Intelligence batch timer elapsed meeting_id=%s segments=%d", self.meeting_id, len(pending))
                await self._analyze(pending)
                pending = []
                continue
            try:
                if item is _STOP:
                    if pending:
                        await self._analyze(pending)
                    return
                assert isinstance(item, TranscriptSegment)
                self.context.append(item)
                pending.append(item)
                if sum(len(part.text) for part in pending) >= self.min_new_chars:
                    logger.debug("Intelligence character threshold reached meeting_id=%s", self.meeting_id)
                    await self._analyze(pending)
                    pending = []
            finally:
                self.queue.task_done()

    async def _analyze(self, pending: list[TranscriptSegment]) -> None:
        if not pending:
            logger.debug("Skipping empty intelligence batch meeting_id=%s", self.meeting_id)
            return
        request = AnalysisRequest(
            meeting_id=self.meeting_id,
            transcripts=list(self.context),
            new_sequence_ids=[item.sequence for item in pending],
            active_insights=list(self.insights.values()),
            active_links=list(self.links.values()),
        )
        try:
            logger.info(
                "Analyzing transcript batch meeting_id=%s segments=%d sequences=%s",
                self.meeting_id,
                len(pending),
                request.new_sequence_ids,
            )
            result = await self.provider.analyze(request)
            self._apply(result)
            await self.on_result(result, request)
        except Exception as exc:
            logger.exception("Intelligence batch failed meeting_id=%s", self.meeting_id)
            if self.on_error:
                await self.on_error(exc, request)
            else:
                raise

    def _apply(self, result: AnalysisResult) -> None:
        for item in result.insights:
            self.insights[item.semantic_key] = InsightState.model_validate(
                item.model_dump(exclude={"action", "confidence", "evidence_sequences", "supersedes_semantic_key"})
            )
        for item in result.links:
            if item.action == "remove":
                self.links.pop(item.semantic_key, None)
            else:
                self.links[item.semantic_key] = LinkState.model_validate(
                    item.model_dump(exclude={"action", "evidence_sequences"})
                )
