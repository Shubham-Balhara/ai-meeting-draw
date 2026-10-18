import asyncio
from typing import Any
from app.meeting.architecture import ArchitectureGraph
from app.meeting.architecture_agent import *
from app.meeting.architecture_agent import _clean_local_transcript, _local_explicit_fallback, _local_extraction_to_result, _strict_schema

def test_agent_prompt_is_complete_and_explicit():
    for section in ("ROLE AND MISSION","TRUST BOUNDARY","PERMITTED ACTIONS","PROHIBITED ACTIONS","MANDATORY PROCESS","INTERPRETATION RULES","STATUS AND PROVENANCE"):
        assert section in ARCHITECTURE_AGENT_PROMPT
    assert len(ARCHITECTURE_AGENT_PROMPT)>5000
    assert "If the current graph is empty" in ARCHITECTURE_AGENT_PROMPT

def test_groq_schema_forbids_additional_properties_on_every_object():
    schema=_strict_schema()
    def visit(value:Any):
        if isinstance(value,dict):
            if value.get("type")=="object":assert value.get("additionalProperties") is False
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(schema)

class Fake:
    async def analyze(self,request):return ArchitectureAgentResult(schema_version="1.0",base_graph_version=request.graph.version,classification="no_op",mutations=[],no_op_reason="chitchat")
    async def close(self):pass

def test_stateful_agent_accepts_valid_noop():
    async def scenario():
        agent=StatefulArchitectureAgent(Fake())
        req=ArchitectureAgentRequest(meeting_id="m",graph=ArchitectureGraph(),transcripts=[AgentTranscript(transcript_id=1,sequence=0,text="hello",start_seconds=0,end_seconds=1)],new_transcript_ids=[1],policy={})
        result,graph=await agent.analyze(req)
        assert result.classification=="no_op" and graph.version==0
    asyncio.run(scenario())

def test_stateful_agent_rejects_silent_noop_for_clear_initial_architecture_claim():
    async def scenario():
        agent=StatefulArchitectureAgent(Fake())
        req=ArchitectureAgentRequest(meeting_id="m",graph=ArchitectureGraph(),transcripts=[AgentTranscript(transcript_id=1,sequence=0,text="The product service calls the checkout service.",start_seconds=0,end_seconds=1)],new_transcript_ids=[1],policy={})
        try:
            await agent.analyze(req)
        except ValueError as exc:
            assert "clear architecture statement" in str(exc)
        else:
            raise AssertionError("clear architecture statement must not be silently ignored")
    asyncio.run(scenario())

def test_local_fallback_compiles_explicit_call_kafka_and_database_claims():
    text=("The product service calls the checkout service. "
          "The checkout service sends a Kafka event to the notification service. "
          "The checkout service will storage data to MySQL database.")
    req=ArchitectureAgentRequest(meeting_id="m",graph=ArchitectureGraph(),
        transcripts=[AgentTranscript(transcript_id=7,sequence=2,text=text,start_seconds=0,end_seconds=5)],
        new_transcript_ids=[7],policy={})
    result=_local_explicit_fallback(req)
    assert result is not None
    graph=ArchitectureValidator().validate_batch(ArchitectureGraph(),result.mutations,
        allowed_transcript_ids={7},new_transcript_ids={7})
    assert {canonical_name(item.name) for item in graph.entities.values()} == {
        "product","checkout","notification","kafka topic","mysql database"
    }
    assert {item.type for item in graph.relationships.values()} == {
        RelationshipType.synchronous_call,RelationshipType.asynchronous_publish,
        RelationshipType.asynchronous_consume,RelationshipType.write,
    }

def test_local_compact_extraction_compiles_to_strict_mutations_with_original_evidence():
    raw=("There will be a product service which communicates with checkout service. "
         "Checkout communicates with notification service through Kafka. "
         "Checkout servicewarelestorage.i8.2 MySQL database.")
    req=ArchitectureAgentRequest(meeting_id="m",graph=ArchitectureGraph(),
        transcripts=[AgentTranscript(transcript_id=9,sequence=3,text=raw,start_seconds=0,end_seconds=5)],
        new_transcript_ids=[9],policy={})
    extraction=LocalArchitectureExtraction(components=[
        LocalArchitectureComponent(name="Product Service",kind="service"),
        LocalArchitectureComponent(name="Checkout Service",kind="service"),
        LocalArchitectureComponent(name="Notification Service",kind="service"),
        LocalArchitectureComponent(name="Kafka",kind="topic"),
        LocalArchitectureComponent(name="MySQL",kind="database"),
    ],relationships=[
        LocalArchitectureRelationship(source="Product Service",target="Checkout Service",kind="synchronous_call",technology=None),
        LocalArchitectureRelationship(source="Checkout Service",target="Notification Service",kind="asynchronous_publish",technology="Kafka"),
        LocalArchitectureRelationship(source="Checkout Service",target="MySQL",kind="write",technology="MySQL"),
    ])
    result=_local_extraction_to_result(req,extraction)
    graph=ArchitectureValidator().validate_batch(ArchitectureGraph(),result.mutations,
        allowed_transcript_ids={9},new_transcript_ids={9})
    assert len(graph.entities)==5 and len(graph.relationships)==4
    assert all(e.quote==raw for mutation in result.mutations for e in mutation.evidence)
    assert "checkout service will store data in a mysql database" in _clean_local_transcript(raw).lower()

def test_architecture_coordinator_drains_in_order():
    async def scenario():
        calls=[]
        async def callback(result,before,after):calls.append((before.version,after.version,result.classification))
        coordinator=ArchitectureCoordinator("m",StatefulArchitectureAgent(Fake()),ArchitectureGraph(),callback)
        await coordinator.submit(AgentTranscript(transcript_id=1,sequence=0,text="hello",start_seconds=0,end_seconds=1))
        await coordinator.submit(AgentTranscript(transcript_id=2,sequence=1,text="thanks",start_seconds=1,end_seconds=2))
        await coordinator.finish()
        assert calls==[(0,0,"no_op"),(0,0,"no_op")]
    asyncio.run(scenario())
