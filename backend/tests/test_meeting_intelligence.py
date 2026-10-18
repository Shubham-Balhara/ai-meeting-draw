import asyncio

import pytest

from app.meeting.intelligence import IntelligenceCoordinator
from app.meeting.providers import IntelligenceProvider, ProviderConfig
from app.meeting.schema import (
    AnalysisResult,
    InsightMutation,
    TranscriptSegment,
    validate_evidence,
    AnalysisRequest,
    InsightKind,
    InsightStatus,
    Relation,
)


class FakeProvider(IntelligenceProvider):
    def __init__(self):
        super().__init__(ProviderConfig(provider="qwen", model="fake", retry_attempts=1))
        self.requests = []
        self.closed = False

    async def analyze(self, request):
        self.requests.append(request)
        sequence = request.new_sequence_ids[-1]
        return AnalysisResult(schema_version="1.0", links=[], summary_delta=None, insights=[InsightMutation(
            action="upsert", semantic_key=f"decision.{sequence}", kind=InsightKind.decision,
            status=InsightStatus.confirmed, text=f"Decision {sequence}", confidence=0.9,
            evidence_sequences=[sequence],
        )])

    async def close(self):
        self.closed = True


def segment(sequence, text="hello"):
    return TranscriptSegment(sequence=sequence, start_seconds=sequence, end_seconds=sequence + 1, text=text)


def test_coordinator_batches_sequentially_and_flushes_tail():
    async def run():
        provider = FakeProvider()
        emitted = []

        async def collect(result, request):
            emitted.append((result, request))

        coordinator = IntelligenceCoordinator(
            "meeting-1", provider, collect, min_new_chars=10, batch_seconds=30,
        )
        await coordinator.submit(segment(0, "12345"))
        await coordinator.submit(segment(1, "67890"))
        await coordinator.submit(segment(2, "tail"))
        await coordinator.finish()
        assert [r.new_sequence_ids for r in provider.requests] == [[0, 1], [2]]
        assert len(emitted) == 2
        assert set(coordinator.insights) == {"decision.1", "decision.2"}
        assert provider.closed

    asyncio.run(run())


def test_evidence_rejects_unknown_sequence():
    request = AnalysisRequest(
        meeting_id="m", transcripts=[segment(1)], new_sequence_ids=[1]
    )
    result = AnalysisResult(schema_version="1.0", links=[], summary_delta=None, insights=[InsightMutation(
        action="upsert", semantic_key="question.api", kind=InsightKind.question,
        status=InsightStatus.open,
        text="Which API?", confidence=0.8, evidence_sequences=[99],
    )])
    with pytest.raises(ValueError, match="unknown evidence"):
        validate_evidence(result, request)


def test_invalid_link_reference_is_rejected():
    from app.meeting.schema import LinkMutation

    request = AnalysisRequest(meeting_id="m", transcripts=[segment(1)], new_sequence_ids=[1])
    result = AnalysisResult(schema_version="1.0", insights=[], summary_delta=None, links=[LinkMutation(
        action="upsert", semantic_key="link.a.b", source_key="a", target_key="b",
        relation=Relation.supports, status="tentative", evidence_sequences=[1],
    )])
    with pytest.raises(ValueError, match="unknown insight keys"):
        validate_evidence(result, request)
