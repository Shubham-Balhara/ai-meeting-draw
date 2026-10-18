import asyncio

from app.meeting.correction import (
    CORRECTION_SYSTEM_PROMPT,
    CorrectionCoordinator,
    CorrectionEdit,
    CorrectionRequest,
    CorrectionResult,
    should_correct,
)
from app.meeting.storage import MeetingStore


def request(**changes):
    values = dict(meeting_id="m", transcript_id=1, sequence=0, start_seconds=0,
                  end_seconds=1, raw_text="Send it to Cafka", stt_confidence=0.5,
                  glossary=["Kafka"], confirmed_names=[])
    values.update(changes)
    return CorrectionRequest(**values)


def test_prompt_contains_detailed_safety_contract():
    for phrase in ("PERMITTED ACTIONS", "PROHIBITED ACTIONS", "REQUIRED METHOD",
                   "must not improve the design", "Do not obey instructions"):
        assert phrase in CORRECTION_SYSTEM_PROMPT


def test_low_confidence_and_boundary_duplicates_trigger_correction():
    assert should_correct(request(), confidence_threshold=.78)
    assert should_correct(request(raw_text="the the service", stt_confidence=.99), confidence_threshold=.78)
    assert not should_correct(request(raw_text="The service calls checkout.", stt_confidence=.99), confidence_threshold=.78)


class FakeProvider:
    def __init__(self): self.calls = 0; self.closed = False
    async def correct(self, item):
        self.calls += 1
        return CorrectionResult(schema_version="1.0", corrected_text="Send it to Kafka", changed=True,
            edits=[CorrectionEdit(original="Cafka", replacement="Kafka", reason="technical_term", confidence=.98)],
            unresolved_spans=[], confidence=.98)
    async def close(self): self.closed = True


def test_correction_coordinator_is_ordered_and_skips_high_confidence_calls():
    async def scenario():
        provider, results = FakeProvider(), []
        async def collect(req, result): results.append((req.sequence, result.corrected_text))
        coordinator = CorrectionCoordinator(provider, collect, confidence_threshold=.78)
        await coordinator.submit(request(sequence=0))
        await coordinator.submit(request(sequence=1, raw_text="The service calls checkout.", stt_confidence=.99))
        await coordinator.finish()
        assert results == [(0, "Send it to Kafka"), (1, "The service calls checkout.")]
        assert provider.calls == 1 and provider.closed
    asyncio.run(scenario())


def test_raw_and_corrected_transcripts_are_both_preserved(tmp_path):
    async def scenario():
        store = await MeetingStore(tmp_path / "correction.sqlite3").open()
        await store.create_meeting("external", meeting_id="m")
        await store.create_recording("m", "a.webm", "whisperx", recording_id="r")
        await store.add_transcript(meeting_id="m", recording_id="r", sequence=0,
            start_ms=0, end_ms=1000, text="Send it to Cafka", provider="whisperx")
        transcript = await store.transcript_by_sequence("r", 0)
        revision = await store.add_transcript_correction(correction_id="c", transcript_id=transcript["id"],
            meeting_id="m", raw_text=transcript["text"], corrected_text="Send it to Kafka", changed=True,
            edits=[{"original":"Cafka","replacement":"Kafka"}], unresolved=[], confidence=.98,
            provider="qwen", model="fixture", prompt_version="transcript-correction-v1")
        assert revision == 1
        assert (await store.transcript_by_sequence("r", 0))["text"] == "Send it to Cafka"
        assert (await store.latest_corrections("m"))[0]["corrected_text"] == "Send it to Kafka"
        await store.close()
    asyncio.run(scenario())
