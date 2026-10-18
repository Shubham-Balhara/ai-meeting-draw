"""Deterministic architecture defaults, identity resolution, and validation."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import unicodedata

from .architecture import (
    ArchitectureGraph, ArchitectureStatus, EntityType, Mutation, Provenance, RelationshipType,
)

logger=logging.getLogger("ai_meeting_draw.meeting.policy")


@dataclass(frozen=True, slots=True)
class ArchitecturePolicy:
    default_backend_style: EntityType = EntityType.microservice
    default_sync_protocol: str = "REST/HTTPS"
    allow_generic_async_infrastructure: bool = True
    default_database_kind: EntityType = EntityType.database
    default_provenance_visibility: bool = True


def canonical_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"\b(the|a|an)\b", " ", normalized)
    normalized = re.sub(r"\b(service|microservice|component)\b", " ", normalized)
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def find_entity(graph: ArchitectureGraph, mention: str, expected_type: EntityType | None = None):
    needle = canonical_name(mention)
    matches = []
    for entity in graph.entities.values():
        names = [entity.name, *entity.aliases]
        if needle and any(canonical_name(name) == needle for name in names):
            if expected_type is None or entity.type == expected_type:
                matches.append(entity)
    return matches[0] if len(matches) == 1 else None


def apply_defaults(mutation: Mutation, policy: ArchitecturePolicy) -> Mutation:
    if mutation.entity and mutation.entity.type == EntityType.backend_service and policy.default_backend_style == EntityType.microservice:
        entity = mutation.entity.model_copy(update={"type":EntityType.microservice, "provenance":Provenance.defaulted,
                                                     "status":ArchitectureStatus.tentative})
        return mutation.model_copy(update={"entity":entity,"provenance":Provenance.defaulted})
    relationship = mutation.relationship
    if relationship and relationship.type == RelationshipType.synchronous_call and not relationship.protocol:
        relationship = relationship.model_copy(update={"protocol":policy.default_sync_protocol,
            "provenance":Provenance.defaulted,"status":ArchitectureStatus.tentative})
        return mutation.model_copy(update={"relationship":relationship,"provenance":Provenance.defaulted})
    return mutation


class ArchitectureValidator:
    def __init__(self, policy: ArchitecturePolicy | None = None): self.policy = policy or ArchitecturePolicy()

    def validate_batch(self, graph: ArchitectureGraph, mutations: list[Mutation], *, allowed_transcript_ids: set[int],
                       new_transcript_ids: set[int]) -> ArchitectureGraph:
        candidate = graph
        fingerprints: set[tuple] = set()
        for original in mutations:
            mutation = apply_defaults(original, self.policy)
            logger.debug("Validating architecture mutation operation=%s target_id=%s provenance=%s evidence=%d",
                         mutation.operation,mutation.target_id,mutation.provenance.value,len(mutation.evidence))
            if mutation.operation != "noop":
                if not mutation.evidence: raise ValueError("non-noop mutation requires evidence")
                evidence_ids = {item.transcript_id for item in mutation.evidence}
                if evidence_ids - allowed_transcript_ids: raise ValueError("mutation references unknown transcript evidence")
                if not evidence_ids & new_transcript_ids: raise ValueError("mutation must cite at least one new transcript")
            if mutation.relationship:
                rel = mutation.relationship
                if rel.type in {RelationshipType.asynchronous_publish,RelationshipType.asynchronous_consume} and rel.protocol and rel.protocol.casefold() == "rest":
                    raise ValueError("asynchronous relationship cannot use REST without explicit custom semantics")
                if rel.type == RelationshipType.synchronous_call and rel.protocol and "kafka" in rel.protocol.casefold():
                    raise ValueError("synchronous Kafka is contradictory and requires a question")
                fingerprint=(rel.source_id,rel.target_id,rel.type.value,rel.protocol or "")
                if fingerprint in fingerprints: raise ValueError("duplicate relationship in mutation batch")
                fingerprints.add(fingerprint)
            candidate = candidate.apply(mutation)
        logger.debug("Architecture batch validation complete base_version=%d resulting_version=%d mutations=%d",
                     graph.version,candidate.version,len(mutations))
        return candidate
