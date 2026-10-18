import asyncio

import pytest

from app.meeting.architecture import (
    ArchitectureEntity, ArchitectureGraph, ArchitectureRelationship, ArchitectureStatus,
    EntityType, Evidence, Mutation, Provenance, RelationshipType,
)
from app.meeting.storage import MeetingStore


def evidence(): return [Evidence(transcript_id=1, sequence=0, quote="Product calls checkout")]


def entity(identifier, name):
    return ArchitectureEntity(id=identifier, name=name, type=EntityType.microservice,
        status=ArchitectureStatus.active, provenance=Provenance.explicit, confidence=.95, evidence=evidence())


def mutation_for_entity(item):
    return Mutation(operation="create_entity", target_id=item.id, entity=item,
        provenance=Provenance.explicit, confidence=.95, evidence=evidence())


def test_graph_requires_existing_relationship_endpoints_and_replays_versions():
    graph = ArchitectureGraph()
    product, checkout = entity("ent_product", "Product"), entity("ent_checkout", "Checkout")
    graph = graph.apply(mutation_for_entity(product)).apply(mutation_for_entity(checkout))
    relationship = ArchitectureRelationship(id="rel_product_checkout", source_id=product.id,
        target_id=checkout.id, type=RelationshipType.synchronous_call, intent="checkout",
        protocol="REST/HTTPS", status=ArchitectureStatus.tentative,
        provenance=Provenance.defaulted, confidence=.8, evidence=evidence())
    graph = graph.apply(Mutation(operation="create_relationship", target_id=relationship.id,
        relationship=relationship, provenance=Provenance.defaulted, confidence=.8, evidence=evidence()))
    assert graph.version == 3 and graph.relationships[relationship.id].protocol == "REST/HTTPS"
    with pytest.raises(ValueError, match="endpoint"):
        ArchitectureGraph().apply(Mutation(operation="create_relationship", target_id=relationship.id,
            relationship=relationship, provenance=Provenance.defaulted, confidence=.8, evidence=evidence()))


def test_graph_mutation_is_compare_and_swap_persisted(tmp_path):
    async def scenario():
        store = await MeetingStore(tmp_path / "architecture.sqlite3").open()
        await store.create_meeting("external", meeting_id="m")
        item = entity("ent_product", "Product")
        mutation = mutation_for_entity(item)
        graph = ArchitectureGraph().apply(mutation)
        version = await store.commit_architecture_mutation(meeting_id="m", mutation_id="mut1",
            operation=mutation.operation, target_id=mutation.target_id,
            mutation_payload=mutation.model_dump(mode="json"), resulting_graph=graph,
            provenance=mutation.provenance.value, confidence=mutation.confidence,
            evidence=[e.model_dump(mode="json") for e in mutation.evidence], base_version=0)
        assert version == 1
        restored = ArchitectureGraph.model_validate(await store.architecture_graph("m"))
        assert restored == graph
        with pytest.raises(ValueError, match="stale"):
            await store.commit_architecture_mutation(meeting_id="m", mutation_id="mut2",
                operation=mutation.operation, target_id=mutation.target_id,
                mutation_payload=mutation.model_dump(mode="json"), resulting_graph=graph,
                provenance="explicit", confidence=.9, evidence=[], base_version=0)
        await store.close()
    asyncio.run(scenario())
