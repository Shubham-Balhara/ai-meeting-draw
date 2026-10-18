from app.meeting.architecture import *
from app.meeting.architecture_diagram import ArchitectureDiagramMapper,NODE_KIND

E=[Evidence(transcript_id=1,sequence=0,quote="evidence")]
def entity(kind,index):return ArchitectureEntity(id=f"ent_{index:02d}",name=kind.value,type=kind,status=ArchitectureStatus.active,provenance=Provenance.explicit,confidence=.9,evidence=E)

def test_every_architecture_entity_has_supported_semantic_shape():
    assert set(NODE_KIND)==set(EntityType)
    graph=ArchitectureGraph(entities={item.id:item for item in (entity(kind,i) for i,kind in enumerate(EntityType))})
    diagram=ArchitectureDiagramMapper().render(graph)
    assert len(diagram["nodes"])==len(EntityType)
    assert all(node["kind"] for node in diagram["nodes"])

def test_defaulted_relationship_is_visible_and_render_is_stable():
    a=entity(EntityType.microservice,1);b=entity(EntityType.microservice,2)
    rel=ArchitectureRelationship(id="rel_aa",source_id=a.id,target_id=b.id,type=RelationshipType.synchronous_call,intent="checkout",protocol="REST/HTTPS",
        status=ArchitectureStatus.tentative,provenance=Provenance.defaulted,confidence=.8,evidence=E)
    graph=ArchitectureGraph(entities={a.id:a,b.id:b},relationships={rel.id:rel})
    mapper=ArchitectureDiagramMapper();first=mapper.render(graph);first["nodes"][0].update(x=123,y=456)
    second=mapper.render(graph,first)
    assert second["nodes"][0]["x"]==123 and second["nodes"][0]["y"]==456
    assert second["edges"][0]["dash"]=="dotted" and "(default)" in second["edges"][0]["label"]
