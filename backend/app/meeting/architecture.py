"""Typed, evidence-backed architecture intermediate representation."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    # JSON persistence represents enums as their canonical strings. Enum
    # membership and every range/pattern remain validated; unknown fields are
    # still rejected.
    model_config = ConfigDict(extra="forbid")


class EntityType(str, Enum):
    person="person"; client="client"; browser_app="browser_app"; mobile_app="mobile_app"; frontend="frontend"
    backend_service="backend_service"; microservice="microservice"; api_gateway="api_gateway"; load_balancer="load_balancer"
    database="database"; cache="cache"; object_storage="object_storage"; queue="queue"; topic="topic"
    message_broker="message_broker"; external_system="external_system"; third_party_api="third_party_api"
    boundary="boundary"; unknown_component="unknown_component"


class RelationshipType(str, Enum):
    synchronous_call="synchronous_call"; asynchronous_publish="asynchronous_publish"; asynchronous_consume="asynchronous_consume"
    read="read"; write="write"; cache_read="cache_read"; cache_write="cache_write"; authentication="authentication"
    file_transfer="file_transfer"; event_flow="event_flow"; dependency="dependency"; containment="containment"
    replication="replication"; unknown_relationship="unknown_relationship"


class Provenance(str, Enum):
    explicit="explicit"; defaulted="defaulted"; inferred="inferred"; suggested="suggested"; questioned="questioned"; confirmed="confirmed"; retracted="retracted"


class ArchitectureStatus(str, Enum):
    tentative="tentative"; active="active"; confirmed="confirmed"; questioned="questioned"; contradicted="contradicted"; retracted="retracted"; superseded="superseded"


class Evidence(StrictModel):
    transcript_id: int = Field(gt=0)
    sequence: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=500)


class ArchitectureEntity(StrictModel):
    id: str = Field(pattern=r"^ent_[a-z0-9][a-z0-9_-]{1,100}$")
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    type: EntityType
    description: str | None = Field(default=None, max_length=1000)
    status: ArchitectureStatus
    provenance: Provenance
    confidence: float = Field(ge=0, le=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(min_length=1, max_length=30)


class ArchitectureRelationship(StrictModel):
    id: str = Field(pattern=r"^rel_[a-z0-9][a-z0-9_-]{1,100}$")
    source_id: str
    target_id: str
    type: RelationshipType
    intent: str = Field(min_length=1, max_length=300)
    protocol: str | None = Field(default=None, max_length=100)
    status: ArchitectureStatus
    provenance: Provenance
    confidence: float = Field(ge=0, le=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def distinct_endpoints(self) -> "ArchitectureRelationship":
        if self.source_id == self.target_id: raise ValueError("relationship endpoints must differ")
        return self


class OpenQuestion(StrictModel):
    id: str = Field(pattern=r"^q_[a-z0-9][a-z0-9_-]{1,100}$")
    text: str = Field(min_length=1, max_length=1000)
    related_ids: list[str] = Field(default_factory=list, max_length=20)
    status: Literal["open","resolved"]
    evidence: list[Evidence] = Field(min_length=1, max_length=30)


class Mutation(StrictModel):
    operation: Literal["create_entity","update_entity","merge_entity","create_relationship","update_relationship","confirm","question","resolve_question","retract","supersede","noop"]
    target_id: str
    entity: ArchitectureEntity | None = None
    relationship: ArchitectureRelationship | None = None
    question: OpenQuestion | None = None
    merge_from_ids: list[str] = Field(default_factory=list)
    provenance: Provenance
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list, max_length=30)


class ArchitectureGraph(StrictModel):
    version: int = Field(default=0, ge=0)
    entities: dict[str, ArchitectureEntity] = Field(default_factory=dict)
    relationships: dict[str, ArchitectureRelationship] = Field(default_factory=dict)
    questions: dict[str, OpenQuestion] = Field(default_factory=dict)

    def apply(self, mutation: Mutation) -> "ArchitectureGraph":
        data = self.model_copy(deep=True)
        op = mutation.operation
        if op in {"create_entity","update_entity"}:
            if mutation.entity is None: raise ValueError(f"{op} requires entity")
            if op == "create_entity" and mutation.entity.id in data.entities: raise ValueError("entity already exists")
            if op == "update_entity" and mutation.entity.id not in data.entities: raise ValueError("entity does not exist")
            data.entities[mutation.entity.id] = mutation.entity
        elif op in {"create_relationship","update_relationship"}:
            if mutation.relationship is None: raise ValueError(f"{op} requires relationship")
            rel = mutation.relationship
            if rel.source_id not in data.entities or rel.target_id not in data.entities: raise ValueError("relationship endpoint does not exist")
            if op == "create_relationship" and rel.id in data.relationships: raise ValueError("relationship already exists")
            if op == "update_relationship" and rel.id not in data.relationships: raise ValueError("relationship does not exist")
            data.relationships[rel.id] = rel
        elif op in {"question","resolve_question"}:
            if mutation.question is None: raise ValueError(f"{op} requires question")
            data.questions[mutation.question.id] = mutation.question
        elif op in {"retract","supersede","confirm"}:
            if mutation.target_id in data.entities:
                status = {"retract":ArchitectureStatus.retracted,"supersede":ArchitectureStatus.superseded,"confirm":ArchitectureStatus.confirmed}[op]
                data.entities[mutation.target_id] = data.entities[mutation.target_id].model_copy(update={"status":status})
            elif mutation.target_id in data.relationships:
                status = {"retract":ArchitectureStatus.retracted,"supersede":ArchitectureStatus.superseded,"confirm":ArchitectureStatus.confirmed}[op]
                data.relationships[mutation.target_id] = data.relationships[mutation.target_id].model_copy(update={"status":status})
            else: raise ValueError("mutation target does not exist")
        elif op == "merge_entity":
            if mutation.target_id not in data.entities: raise ValueError("merge target does not exist")
            for old in mutation.merge_from_ids:
                if old not in data.entities: raise ValueError("merge source does not exist")
                del data.entities[old]
                for rid, rel in list(data.relationships.items()):
                    data.relationships[rid] = rel.model_copy(update={"source_id":mutation.target_id if rel.source_id==old else rel.source_id,
                                                                     "target_id":mutation.target_id if rel.target_id==old else rel.target_id})
        data.version += 1
        return data
