"""Strict, provider-neutral contracts for meeting intelligence."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InsightKind(str, Enum):
    topic = "topic"
    proposal = "proposal"
    question = "question"
    decision = "decision"
    action_item = "action_item"
    risk = "risk"
    blocker = "blocker"
    assumption = "assumption"
    note = "note"


class InsightStatus(str, Enum):
    tentative = "tentative"
    open = "open"
    confirmed = "confirmed"
    rejected = "rejected"
    resolved = "resolved"
    superseded = "superseded"


class Relation(str, Enum):
    depends_on = "depends_on"
    blocks = "blocks"
    answers = "answers"
    implements = "implements"
    contradicts = "contradicts"
    supports = "supports"
    relates_to = "relates_to"


class TranscriptSegment(StrictModel):
    sequence: int = Field(ge=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=20_000)

    @model_validator(mode="after")
    def valid_time_range(self) -> "TranscriptSegment":
        if self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds must not precede start_seconds")
        return self


class InsightState(StrictModel):
    semantic_key: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9][a-z0-9._:-]*$")
    kind: InsightKind
    status: InsightStatus
    text: str = Field(min_length=1, max_length=2_000)
    owner: str | None = Field(default=None, max_length=200)
    due_date: date | None = None


class InsightMutation(InsightState):
    action: Literal["upsert", "transition", "supersede"]
    confidence: float = Field(ge=0, le=1)
    evidence_sequences: list[int] = Field(min_length=1, max_length=20)
    supersedes_semantic_key: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def valid_supersession(self) -> "InsightMutation":
        needs_key = self.action == "supersede" or self.status == InsightStatus.superseded
        if needs_key and not self.supersedes_semantic_key:
            raise ValueError("supersedes_semantic_key is required for supersession")
        return self


class LinkState(StrictModel):
    semantic_key: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9][a-z0-9._:-]*$")
    source_key: str = Field(min_length=1, max_length=160)
    target_key: str = Field(min_length=1, max_length=160)
    relation: Relation
    status: Literal["tentative", "confirmed", "rejected"]


class LinkMutation(LinkState):
    action: Literal["upsert", "remove"]
    evidence_sequences: list[int] = Field(min_length=1, max_length=20)


class AnalysisRequest(StrictModel):
    meeting_id: str = Field(min_length=1, max_length=200)
    transcripts: list[TranscriptSegment] = Field(min_length=1, max_length=200)
    new_sequence_ids: list[int] = Field(min_length=1, max_length=200)
    active_insights: list[InsightState] = Field(default_factory=list, max_length=500)
    active_links: list[LinkState] = Field(default_factory=list, max_length=1_000)


class AnalysisResult(StrictModel):
    # All output properties are required (nullable where appropriate) for
    # compatibility with providers that enforce strict JSON Schema output.
    schema_version: Literal["1.0"]
    insights: list[InsightMutation] = Field(max_length=100)
    links: list[LinkMutation] = Field(max_length=200)
    summary_delta: str | None = Field(max_length=4_000)


def validate_evidence(result: AnalysisResult, request: AnalysisRequest) -> None:
    """Reject hallucinated transcript references and dangling relationship keys."""
    allowed_sequences = {item.sequence for item in request.transcripts}
    known_keys = {item.semantic_key for item in request.active_insights}
    known_keys.update(item.semantic_key for item in result.insights)
    for mutation in [*result.insights, *result.links]:
        unknown = set(mutation.evidence_sequences) - allowed_sequences
        if unknown:
            raise ValueError(f"unknown evidence sequences: {sorted(unknown)}")
    for link in result.links:
        missing = {link.source_key, link.target_key} - known_keys
        if missing:
            raise ValueError(f"link references unknown insight keys: {sorted(missing)}")
