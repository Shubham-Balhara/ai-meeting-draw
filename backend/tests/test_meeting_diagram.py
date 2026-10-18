import asyncio

from app.meeting.diagram import DiagramAdapter, DiagramUpdateService, MemoryBindings
from app.meeting.schema import (
    AnalysisResult, InsightKind, InsightMutation, InsightStatus, LinkMutation, Relation,
)


def node(key, **extra):
    return {"op": "upsert_node", "semantic_key": key, "category": "suggestion", "status": "proposed", "text": key, **extra}


def test_replay_is_idempotent_and_preserves_human_objects_and_layout():
    human = {"id": "human", "kind": "rect", "x": 999, "y": 4, "w": 10, "h": 10, "text": "mine", "fill": "white", "stroke": "black", "strokeWidth": 1}
    adapter = DiagramAdapter(MemoryBindings())
    first = adapter.apply({"pages": [{"id": "p1", "nodes": [human], "edges": []}]}, [node("idea")])
    generated = first["pages"][0]["nodes"][1]
    generated["x"] = 777
    generated["groupId"] = "human-group"
    second = adapter.apply(first, [node("idea", status="confirmed", category="decision", text="Approved")])
    assert len(second["pages"][0]["nodes"]) == 2
    assert second["pages"][0]["nodes"][0] == human
    changed = second["pages"][0]["nodes"][1]
    assert (changed["x"], changed["groupId"]) == (777, "human-group")
    assert changed["strokeDash"] == "solid"
    assert changed["text"] == "Approved"


def test_question_resolves_in_place_and_edges_use_stable_bindings():
    bindings = MemoryBindings()
    adapter = DiagramAdapter(bindings)
    diagram = adapter.apply(None, [node("a", category="question", status="open"), node("b", category="fact", status="confirmed")])
    question_id = bindings.get("node", "a")
    diagram = adapter.apply(diagram, [{"op": "upsert_edge", "semantic_key": "a_to_b", "source_semantic_key": "a", "target_semantic_key": "b", "status": "proposed", "relation": "depends on"}])
    assert diagram["edges"][0]["dash"] == "dotted"
    diagram = adapter.apply(diagram, [node("a", category="question", status="resolved", text="Answered")])
    question = next(n for n in diagram["nodes"] if n["id"] == question_id)
    assert question["stroke"] == "#16a34a"
    assert question["strokeDash"] == "solid"


def test_remove_cannot_touch_unbound_human_object():
    adapter = DiagramAdapter(MemoryBindings())
    diagram = {"nodes": [{"id": "human"}], "edges": []}
    result = adapter.apply(diagram, [{"op": "remove", "semantic_key": "unknown", "object_kind": "node"}])
    assert result == diagram


def test_analysis_result_contract_is_consumed_directly():
    adapter = DiagramAdapter(MemoryBindings())
    result = AnalysisResult(
        schema_version="1.0",
        insights=[
            InsightMutation(action="upsert", semantic_key="cache", kind=InsightKind.proposal, status=InsightStatus.tentative, text="Add cache", confidence=0.8, evidence_sequences=[1]),
            InsightMutation(action="upsert", semantic_key="api", kind=InsightKind.topic, status=InsightStatus.confirmed, text="API", confidence=1.0, evidence_sequences=[1]),
        ],
        links=[LinkMutation(action="upsert", semantic_key="cache-api", source_key="cache", target_key="api", relation=Relation.supports, status="tentative", evidence_sequences=[1])],
        summary_delta=None,
    )
    diagram = adapter.apply_analysis(None, result)
    assert diagram["nodes"][0]["strokeDash"] == "dotted"
    assert diagram["edges"][0]["dash"] == "dotted"


def test_async_update_service_loads_saves_and_broadcasts():
    events = []
    async def load(_): return {"diagram": {"nodes": [], "edges": []}}
    async def save(board, diagram): events.append(("save", board, diagram))
    async def broadcast(board, frame): events.append(("broadcast", board, frame))
    service = DiagramUpdateService(DiagramAdapter(MemoryBindings()), load=load, save=save, broadcast=broadcast)
    result = asyncio.run(service.apply("board", [node("idea")], page_id="p1"))
    assert len(result["nodes"]) == 1
    assert [e[0] for e in events] == ["save", "broadcast"]
    assert events[1][2]["pageId"] == "p1"
