import asyncio
import json

from app.meeting.config import MeetingSettings
from app.meeting.stt import STTResult, Transcriber
from app.meeting.transcription import AudioSegment, TranscriptionPipeline


class FakeTranscriber(Transcriber):
    name = "fake"
    model = "fixture"

    async def transcribe(self, pcm: bytes) -> STTResult:
        return STTResult(
            text="Kafka event",
            language="en",
            confidence=0.8,
            average_log_probability=-0.223,
            no_speech_probability=0.02,
            metadata={"segment_count": 1},
        )


def test_rich_stt_result_becomes_provider_neutral_event():
    async def scenario():
        events = []
        pipeline = TranscriptionPipeline(FakeTranscriber(), MeetingSettings(), events.append)
        pipeline.queue = asyncio.Queue()
        segment = AudioSegment(3, b"\0\0" * 1600, 1.0, 1.1)
        await pipeline.queue.put(segment)
        await pipeline.queue.put(None)
        worker = asyncio.create_task(pipeline._worker())
        await worker
        event = pipeline.results[3]
        assert event["text"] == "Kafka event"
        assert event["confidence"] == 0.8
        assert event["average_log_probability"] == -0.223
        assert event["no_speech_probability"] == 0.02
        assert event["stt_metadata"] == {"segment_count": 1}

    asyncio.run(scenario())


def test_stt_settings_validate_vad_ranges():
    settings = MeetingSettings(whisperx_vad_onset=1.1)
    try:
        settings.validate()
    except ValueError as exc:
        assert "WhisperX VAD" in str(exc)
    else:
        raise AssertionError("invalid VAD setting was accepted")


def test_stt_result_metadata_is_json_serializable():
    result = STTResult(text="hello", metadata={"segment_count": 1})
    assert json.loads(json.dumps(result.metadata)) == {"segment_count": 1}
