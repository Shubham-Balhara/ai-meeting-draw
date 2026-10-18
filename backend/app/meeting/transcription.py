from __future__ import annotations

import asyncio
import contextlib
from collections import deque
import logging
import math
from array import array
from dataclasses import dataclass
from typing import Awaitable, Callable

from .config import MeetingSettings
from .stt import SAMPLE_RATE, Transcriber


BYTES_PER_SECOND = SAMPLE_RATE * 2
EventSink = Callable[[dict], Awaitable[None]]
logger = logging.getLogger("ai_meeting_draw.meeting.transcription")


@dataclass(frozen=True)
class AudioSegment:
    sequence: int
    pcm: bytes
    start_seconds: float
    end_seconds: float


class UtteranceSegmenter:
    """Streaming energy VAD with bounded pre-roll and utterance duration.

    This intentionally performs only segmentation. WhisperX still performs its
    own higher-quality VAD. The lightweight gate prevents long silence windows
    from being sent to any STT provider and gives both providers natural speech
    boundaries without adding a native dependency to the backend.
    """

    def __init__(self, settings: MeetingSettings):
        self.frame_bytes = SAMPLE_RATE * 2 * settings.vad_frame_ms // 1000
        self.threshold = settings.vad_energy_threshold
        self.onset_frames = max(1, math.ceil(settings.vad_onset_ms / settings.vad_frame_ms))
        self.offset_frames = max(1, math.ceil(settings.vad_offset_ms / settings.vad_frame_ms))
        self.pre_frames = max(0, math.ceil(settings.vad_pre_speech_ms / settings.vad_frame_ms))
        self.min_speech_frames = max(1, math.ceil(settings.vad_min_speech_ms / settings.vad_frame_ms))
        self.max_frames = max(1, math.ceil(settings.vad_max_utterance_seconds * 1000 / settings.vad_frame_ms))
        self.pending = bytearray()
        # Keep the requested pre-speech context *plus* the frames required to
        # confirm onset; otherwise the onset run displaces all useful padding.
        self.pre_roll: deque[tuple[int, bytes]] = deque(maxlen=max(1, self.pre_frames + self.onset_frames))
        self.active: list[tuple[int, bytes]] = []
        self.frame_index = 0
        self.voiced_run = 0
        self.silent_run = 0
        self.voiced_total = 0

    @staticmethod
    def _rms(frame: bytes) -> float:
        samples = array("h")
        samples.frombytes(frame)
        if not samples:
            return 0.0
        if samples.itemsize == 2 and __import__("sys").byteorder != "little":
            samples.byteswap()
        return math.sqrt(sum(sample * sample for sample in samples) / len(samples))

    def feed(self, data: bytes) -> list[tuple[float, float, bytes]]:
        self.pending.extend(data)
        emitted: list[tuple[float, float, bytes]] = []
        while len(self.pending) >= self.frame_bytes:
            frame = bytes(self.pending[:self.frame_bytes])
            del self.pending[:self.frame_bytes]
            emitted.extend(self._frame(frame))
        return emitted

    def _frame(self, frame: bytes) -> list[tuple[float, float, bytes]]:
        index = self.frame_index
        self.frame_index += 1
        voiced = self._rms(frame) >= self.threshold
        if not self.active:
            self.pre_roll.append((index, frame))
            self.voiced_run = self.voiced_run + 1 if voiced else 0
            if self.voiced_run >= self.onset_frames:
                self.active = list(self.pre_roll)
                self.voiced_total = self.voiced_run
                self.silent_run = 0
                self.pre_roll.clear()
            return []

        self.active.append((index, frame))
        if voiced:
            self.voiced_total += 1
            self.silent_run = 0
        else:
            self.silent_run += 1
        if self.silent_run >= self.offset_frames or len(self.active) >= self.max_frames:
            return [segment] if (segment := self._emit()) else []
        return []

    def _emit(self) -> tuple[float, float, bytes] | None:
        active, voiced = self.active, self.voiced_total
        self.active = []
        self.voiced_total = self.voiced_run = self.silent_run = 0
        if not active or voiced < self.min_speech_frames:
            return None
        start_frame, end_frame = active[0][0], active[-1][0] + 1
        return start_frame * self.frame_bytes / BYTES_PER_SECOND, end_frame * self.frame_bytes / BYTES_PER_SECOND, b"".join(frame for _, frame in active)

    def finish(self) -> list[tuple[float, float, bytes]]:
        emitted: list[tuple[float, float, bytes]] = []
        if self.pending:
            self.pending.extend(b"\0" * (self.frame_bytes - len(self.pending)))
            emitted.extend(self._frame(bytes(self.pending)))
            self.pending.clear()
        segment = self._emit()
        if segment:
            emitted.append(segment)
        return emitted


class TranscriptionPipeline:
    """Continuous WebM decoder with concurrent STT and ordered durable sink."""

    def __init__(self, transcriber: Transcriber, settings: MeetingSettings, sink: EventSink):
        self.transcriber, self.settings, self.sink = transcriber, settings, sink
        self.queue: asyncio.Queue[AudioSegment | None] = asyncio.Queue(settings.transcription_queue_size)
        self.results: dict[int, dict] = {}
        self.condition = asyncio.Condition()
        self.next_to_send = self.sequence = 0
        self.decoder: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.sender: asyncio.Task | None = None
        self.workers: list[asyncio.Task] = []
        self.buffer = bytearray()
        self.segmenter = UtteranceSegmenter(settings) if settings.utterance_vad_enabled else None
        self.closed = False

    async def start(self) -> None:
        logger.info(
            "Starting audio decoder segment_seconds=%s overlap_seconds=%s workers=%s utterance_vad=%s",
            self.settings.segment_seconds,
            self.settings.segment_overlap_seconds,
            self.settings.transcription_workers,
            self.settings.utterance_vad_enabled,
        )
        self.decoder = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "webm", "-i", "pipe:0",
            "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        self.reader = asyncio.create_task(self._read_pcm())
        self.workers = [asyncio.create_task(self._worker()) for _ in range(self.settings.transcription_workers)]
        self.sender = asyncio.create_task(self._ordered_sender())
        logger.debug("Transcription pipeline tasks started")

    async def write(self, chunk: bytes) -> None:
        if not self.decoder or not self.decoder.stdin or self.decoder.stdin.is_closing():
            raise RuntimeError("ffmpeg decoder is unavailable")
        self.decoder.stdin.write(chunk); await self.decoder.stdin.drain()
        logger.debug("Forwarded encoded audio chunk bytes=%d", len(chunk))

    async def _read_pcm(self) -> None:
        assert self.decoder and self.decoder.stdout
        size = int(self.settings.segment_seconds * BYTES_PER_SECOND)
        overlap = int(self.settings.segment_overlap_seconds * BYTES_PER_SECOND)
        while data := await self.decoder.stdout.read(65536):
            logger.debug("Decoded PCM bytes=%d buffered_before=%d", len(data), len(self.buffer))
            if self.segmenter:
                for start, end, pcm in self.segmenter.feed(data):
                    await self._enqueue(pcm, start_seconds=start, end_seconds=end)
            else:
                self.buffer.extend(data)
                while len(self.buffer) >= size:
                    await self._enqueue(bytes(self.buffer[:size])); del self.buffer[:size-overlap]

    async def _enqueue(self, pcm: bytes, *, start_seconds: float | None = None, end_seconds: float | None = None) -> None:
        start = start_seconds if start_seconds is not None else self.sequence * (self.settings.segment_seconds-self.settings.segment_overlap_seconds)
        end = end_seconds if end_seconds is not None else start + len(pcm)/BYTES_PER_SECOND
        logger.debug(
            "Queued PCM segment sequence=%d start=%.3f duration=%.3f bytes=%d",
            self.sequence,
            start,
            end - start,
            len(pcm),
        )
        await self.queue.put(AudioSegment(self.sequence, pcm, start, end)); self.sequence += 1

    async def _worker(self) -> None:
        while True:
            segment = await self.queue.get()
            if segment is None: self.queue.task_done(); return
            try:
                logger.debug("Transcribing segment sequence=%d", segment.sequence)
                result = await self.transcriber.transcribe(segment.pcm)
                if result.text:
                    event = {"type":"transcript","sequence":segment.sequence,"start_seconds":round(segment.start_seconds,3),
                             "end_seconds":round(segment.end_seconds,3),"text":result.text,"is_final":True,
                             "provider":self.transcriber.name,"model":self.transcriber.model,
                             "language":result.language,"confidence":result.confidence,
                             "average_log_probability":result.average_log_probability,
                             "no_speech_probability":result.no_speech_probability,"stt_metadata":result.metadata}
                    logger.info("Transcript generated sequence=%d characters=%d", segment.sequence, len(result.text))
                else:
                    event = {"type":"transcript_skipped","sequence":segment.sequence,"reason":"no_speech"}
                    logger.debug("Ignoring segment with no detected speech sequence=%d", segment.sequence)
            except Exception as exc:
                logger.exception("Transcription failed sequence=%d", segment.sequence)
                event = {"type":"transcription_error","sequence":segment.sequence,"message":str(exc),"provider":self.transcriber.name}
            async with self.condition:
                self.results[segment.sequence]=event; self.condition.notify_all()
            self.queue.task_done()

    async def _ordered_sender(self) -> None:
        while True:
            async with self.condition:
                await self.condition.wait_for(lambda: self.next_to_send in self.results or self.closed)
                if self.next_to_send not in self.results: return
                event=self.results.pop(self.next_to_send); self.next_to_send += 1
            if event["type"] == "transcript_skipped":
                logger.debug("Skipped silent segment sequence=%d", event["sequence"])
                continue
            await self.sink(event)

    async def finish(self) -> None:
        if self.closed: return
        logger.info("Finalizing transcription pipeline")
        if self.decoder and self.decoder.stdin and not self.decoder.stdin.is_closing():
            self.decoder.stdin.close(); await self.decoder.stdin.wait_closed()
        if self.reader: await self.reader
        if self.segmenter:
            for start, end, pcm in self.segmenter.finish():
                await self._enqueue(pcm, start_seconds=start, end_seconds=end)
        elif len(self.buffer) >= int(self.settings.min_final_segment_seconds*BYTES_PER_SECOND):
            await self._enqueue(bytes(self.buffer))
        self.buffer.clear(); await self.queue.join()
        for _ in self.workers: await self.queue.put(None)
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.closed=True
        async with self.condition: self.condition.notify_all()
        if self.sender: await self.sender
        if self.decoder: await self.decoder.wait()
        await self.transcriber.close()
        logger.info("Transcription pipeline completed segments=%d", self.sequence)

    async def abort(self) -> None:
        logger.warning("Aborting transcription pipeline")
        self.closed=True
        if self.decoder and self.decoder.returncode is None: self.decoder.kill(); await self.decoder.wait()
        for task in [self.reader,self.sender,*self.workers]:
            if task: task.cancel()
        with contextlib.suppress(Exception): await self.transcriber.close()
