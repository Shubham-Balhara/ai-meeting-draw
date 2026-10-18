import pytest

from app.meeting.architecture import *
from app.meeting.policy import ArchitecturePolicy, ArchitectureValidator, apply_defaults, canonical_name, find_entity


E=[Evidence(transcript_id=1,sequence=0,quote="Product calls checkout")]


def node(identifier,name,kind=EntityType.backend_service):
    return ArchitectureEntity(id=identifier,name=name,type=kind,status=ArchitectureStatus.active,
        provenance=Provenance.explicit,confidence=.9,evidence=E)


def test_identity_normalization_and_unique_resolution():
    assert canonical_name("The Checkout Service") == "checkout"
    item=node("ent_checkout","Checkout Service")
    graph=ArchitectureGraph(entities={item.id:item})
    assert find_entity(graph,"checkout").id == item.id


def test_defaults_are_visible_and_vendor_neutral():
    item=node("ent_backend","Backend Service")
    mutation=Mutation(operation="create_entity",target_id=item.id,entity=item,provenance=Provenance.explicit,confidence=.9,evidence=E)
    defaulted=apply_defaults(mutation,ArchitecturePolicy())
    assert defaulted.entity.type == EntityType.microservice
    assert defaulted.entity.provenance == Provenance.defaulted


def test_validator_adds_rest_default_and_rejects_unknown_evidence():
    a=node("ent_product","Product",EntityType.microservice); b=node("ent_checkout","Checkout",EntityType.microservice)
    graph=ArchitectureGraph(entities={a.id:a,b.id:b})
    rel=ArchitectureRelationship(id="rel_product_checkout",source_id=a.id,target_id=b.id,
        type=RelationshipType.synchronous_call,intent="checkout",status=ArchitectureStatus.active,
        provenance=Provenance.explicit,confidence=.9,evidence=E)
    mutation=Mutation(operation="create_relationship",target_id=rel.id,relationship=rel,
        provenance=Provenance.explicit,confidence=.9,evidence=E)
    result=ArchitectureValidator().validate_batch(graph,[mutation],allowed_transcript_ids={1},new_transcript_ids={1})
    assert result.relationships[rel.id].protocol == "REST/HTTPS"
    assert result.relationships[rel.id].provenance == Provenance.defaulted
    with pytest.raises(ValueError,match="unknown"):
        ArchitectureValidator().validate_batch(graph,[mutation],allowed_transcript_ids={2},new_transcript_ids={2})


def test_validator_rejects_synchronous_kafka_conflict():
    a=node("ent_aa","A",EntityType.microservice); b=node("ent_bb","B",EntityType.microservice)
    graph=ArchitectureGraph(entities={a.id:a,b.id:b})
    rel=ArchitectureRelationship(id="rel_a_b",source_id=a.id,target_id=b.id,type=RelationshipType.synchronous_call,
        intent="call",protocol="Kafka",status=ArchitectureStatus.active,provenance=Provenance.explicit,confidence=.9,evidence=E)
    mutation=Mutation(operation="create_relationship",target_id=rel.id,relationship=rel,
        provenance=Provenance.explicit,confidence=.9,evidence=E)
    with pytest.raises(ValueError,match="contradictory"):
        ArchitectureValidator().validate_batch(graph,[mutation],allowed_transcript_ids={1},new_transcript_ids={1})
