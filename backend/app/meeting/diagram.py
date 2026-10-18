"""Deterministic meeting-intelligence to AI Meeting Draw diagram adapter.

The language model decides *meaning* and returns semantic mutations.  This
module owns board IDs, layout and visual styling so replaying the same insight
is idempotent and changing providers cannot randomly rearrange the board.

The adapter deliberately edits only objects whose IDs came from ``bindings``.
Human-created objects and all other pages are passed through untouched.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable, Protocol

logger = logging.getLogger("ai_meeting_draw.meeting.diagram")


class BindingStore(Protocol):
    """Persistent semantic-key -> AI Meeting Draw object-ID mapping."""

    def get_or_create(self, object_kind: str, semantic_key: str) -> str: ...

    def get(self, object_kind: str, semantic_key: str) -> str | None: ...


class MemoryBindings:
    """Small test/dev implementation; production should use SQLite."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    def get_or_create(self, object_kind: str, semantic_key: str) -> str:
        key = (object_kind, semantic_key)
        if key not in self._values:
            digest = hashlib.sha256(f"{object_kind}:{semantic_key}".encode()).hexdigest()[:16]
            self._values[key] = f"mi_{object_kind[0]}_{digest}"
        return self._values[key]

    def get(self, object_kind: str, semantic_key: str) -> str | None:
        return self._values.get((object_kind, semantic_key))


@dataclass(frozen=True)
class Layout:
    left: float = 120
    top: float = 120
    column_gap: float = 260
    row_gap: float = 180
    columns: int = 4
    width: float = 190
    height: float = 96


_STYLE = {
    "suggestion": {"fill": "#f3f4f6", "stroke": "#6b7280", "textColor": "#4b5563", "strokeDash": "dotted"},
    "question": {"fill": "#fef2f2", "stroke": "#dc2626", "textColor": "#991b1b", "strokeDash": "dashed"},
    "decision": {"fill": "#eef4ff", "stroke": "#2563eb", "textColor": "#1e3a8a", "strokeDash": "solid"},
    "fact": {"fill": "#f0fdf4", "stroke": "#16a34a", "textColor": "#14532d", "strokeDash": "solid"},
    "action_item": {"fill": "#fffbeb", "stroke": "#d97706", "textColor": "#78350f", "strokeDash": "solid"},
    "risk": {"fill": "#fff7ed", "stroke": "#ea580c", "textColor": "#7c2d12", "strokeDash": "solid"},
}

_KIND = {
    "suggestion": "rounded",
    "question": "callout",
    "decision": "diamond",
    "fact": "rect",
    "action_item": "sticky",
    "risk": "note",
}


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    raise TypeError("mutation/result must be a mapping or Pydantic model")


def _category(kind: str) -> str:
    return {
        "proposal": "suggestion", "question": "question", "decision": "decision",
        "action_item": "action_item", "risk": "risk", "blocker": "risk",
        "topic": "fact", "assumption": "fact", "note": "fact",
    }.get(kind, "fact")


def _style(category: str, status: str) -> dict[str, Any]:
    category = category if category in _STYLE else "fact"
    result = dict(_STYLE[category])
    if status in {"tentative", "proposed", "open"} and category not in {"question", "suggestion"}:
        result["strokeDash"] = "dotted"
    if status in {"rejected", "superseded"}:
        result.update(fill="#f3f4f6", stroke="#9ca3af", textColor="#6b7280", opacity=0.45, strokeDash="dashed")
    elif status == "resolved" and category == "question":
        result = dict(_STYLE["fact"])
    return result


def _text(mutation: dict[str, Any]) -> str:
    text = str(mutation.get("text") or "").strip()
    details = []
    if mutation.get("owner"):
        details.append(f"Owner: {mutation['owner']}")
    due = mutation.get("due_at", mutation.get("due_date"))
    if due:
        details.append(f"Due: {due}")
    return "\n".join([text, *details])


class DiagramAdapter:
    def __init__(self, bindings: BindingStore, layout: Layout | None = None) -> None:
        self.bindings = bindings
        self.layout = layout or Layout()

    def apply(
        self,
        diagram: dict[str, Any] | None,
        mutations: list[dict[str, Any]],
        *,
        page_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a copied diagram with semantic mutations applied.

        Supported operations are ``upsert_node``, ``upsert_edge`` and
        ``remove``/``supersede``.  ``remove`` only affects an object with a
        known meeting binding; it can never delete an unrelated board object.
        """
        out = copy.deepcopy(diagram) if isinstance(diagram, dict) else {}
        logger.debug("Applying diagram mutations count=%d page_id=%s", len(mutations), page_id)
        page = self._page(out, page_id)
        nodes = page.setdefault("nodes", [])
        edges = page.setdefault("edges", [])

        for raw_mutation in mutations:
            mutation = _mapping(raw_mutation)
            op = mutation.get("op")
            if op == "upsert_node":
                self._upsert_node(nodes, mutation)
            elif op == "upsert_edge":
                self._upsert_edge(edges, mutation)
            elif op == "mark_superseded":
                self._mark_superseded(nodes, mutation)
            elif op in {"remove", "supersede"}:
                self._remove(nodes, edges, mutation)
            else:
                logger.warning("Ignoring unsupported diagram operation op=%s", op)
        logger.debug("Diagram mutations applied nodes=%d edges=%d", len(nodes), len(edges))
        return out

    def apply_analysis(
        self,
        diagram: dict[str, Any] | None,
        result: Any,
        *,
        page_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply a meeting AnalysisResult or its JSON-deserialized mapping."""
        payload = _mapping(result)
        mutations: list[dict[str, Any]] = []
        for raw in payload.get("insights", []):
            item = _mapping(raw)
            action = item.get("action", "upsert")
            item["op"] = "upsert_node"
            item["category"] = _category(str(item.get("kind", "note")))
            mutations.append(item)
            if action == "supersede" and item.get("supersedes_semantic_key"):
                mutations.append({
                    "op": "mark_superseded",
                    "semantic_key": item["supersedes_semantic_key"],
                })
        for raw in payload.get("links", []):
            item = _mapping(raw)
            item.update(
                op="remove" if item.get("action") == "remove" else "upsert_edge",
                object_kind="edge",
                source_semantic_key=item.get("source_key"),
                target_semantic_key=item.get("target_key"),
            )
            mutations.append(item)
        return self.apply(diagram, mutations, page_id=page_id)

    @staticmethod
    def _page(diagram: dict[str, Any], page_id: str | None) -> dict[str, Any]:
        if isinstance(diagram.get("pages"), list):
            pages = diagram["pages"]
            if page_id:
                for page in pages:
                    if page.get("id") == page_id:
                        return page
            if pages:
                return pages[-1]
            page = {"id": page_id or "meeting", "name": "Meeting", "nodes": [], "edges": []}
            pages.append(page)
            return page
        diagram.setdefault("nodes", [])
        diagram.setdefault("edges", [])
        return diagram

    def _next_position(self, nodes: list[dict[str, Any]]) -> tuple[float, float]:
        occupied = {
            (round((float(n.get("x", 0)) - self.layout.left) / self.layout.column_gap),
             round((float(n.get("y", 0)) - self.layout.top) / self.layout.row_gap))
            for n in nodes
        }
        slot = 0
        while True:
            col, row = slot % self.layout.columns, slot // self.layout.columns
            if (col, row) not in occupied:
                return (self.layout.left + col * self.layout.column_gap,
                        self.layout.top + row * self.layout.row_gap)
            slot += 1

    def _upsert_node(self, nodes: list[dict[str, Any]], mutation: dict[str, Any]) -> None:
        key = str(mutation.get("semantic_key") or mutation.get("insight_id") or "").strip()
        if not key:
            raise ValueError("upsert_node requires semantic_key or insight_id")
        node_id = self.bindings.get_or_create("node", key)
        existing = next((n for n in nodes if n.get("id") == node_id), None)
        category = str(mutation.get("category") or "fact")
        status = str(mutation.get("status") or "confirmed")
        fields = {
            "id": node_id,
            "kind": _KIND.get(category, "rect"),
            "text": _text(mutation),
            "strokeWidth": 2,
            "wrap": True,
            **_style(category, status),
        }
        if existing is None:
            x, y = self._next_position(nodes)
            fields.update(x=x, y=y, w=self.layout.width, h=self.layout.height)
            nodes.append(fields)
        else:
            # Preserve human layout, size, grouping, z-order and other editor fields.
            existing.update(fields)

    def _upsert_edge(self, edges: list[dict[str, Any]], mutation: dict[str, Any]) -> None:
        key = str(mutation.get("semantic_key") or mutation.get("relationship_id") or "").strip()
        source_key = str(mutation.get("source_semantic_key") or "").strip()
        target_key = str(mutation.get("target_semantic_key") or "").strip()
        if not key or not source_key or not target_key:
            raise ValueError("upsert_edge requires semantic/relationship id and source/target semantic keys")
        source_id = self.bindings.get("node", source_key)
        target_id = self.bindings.get("node", target_key)
        if not source_id or not target_id:
            raise ValueError("upsert_edge endpoints must be bound by prior node mutations")
        edge_id = self.bindings.get_or_create("edge", key)
        existing = next((e for e in edges if e.get("id") == edge_id), None)
        status = str(mutation.get("status") or "confirmed")
        proposed = status in {"tentative", "proposed", "open"}
        rejected = status in {"rejected", "superseded"}
        fields = {
            "id": edge_id,
            "source": {"kind": "floating", "nodeId": source_id},
            "target": {"kind": "floating", "nodeId": target_id},
            "routing": "elbow",
            "stroke": "#9ca3af" if rejected else ("#6b7280" if proposed else "#2d3142"),
            "strokeWidth": 2,
            "endArrow": True,
            "startArrow": False,
            "endHead": "arrow",
            "startHead": "none",
            "dash": "dotted" if proposed else ("dashed" if rejected else "solid"),
            "label": str(mutation.get("label") or mutation.get("relation") or ""),
            "animated": False,
        }
        if existing is None:
            edges.append(fields)
        else:
            # Deliberately preserve user-owned waypoints and z.
            existing.update(fields)

    def _mark_superseded(self, nodes: list[dict[str, Any]], mutation: dict[str, Any]) -> None:
        node_id = self.bindings.get("node", str(mutation.get("semantic_key") or ""))
        node = next((item for item in nodes if item.get("id") == node_id), None)
        if node:
            node.update(fill="#f3f4f6", stroke="#9ca3af", textColor="#6b7280",
                        opacity=0.45, strokeDash="dashed")

    def _remove(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]], mutation: dict[str, Any]) -> None:
        key = str(mutation.get("semantic_key") or "").strip()
        kind = str(mutation.get("object_kind") or "node")
        object_id = self.bindings.get(kind, key) if key else None
        if not object_id:
            return
        if kind == "edge":
            edges[:] = [e for e in edges if e.get("id") != object_id]
            return
        nodes[:] = [n for n in nodes if n.get("id") != object_id]
        edges[:] = [e for e in edges if e.get("source", {}).get("nodeId") != object_id and e.get("target", {}).get("nodeId") != object_id]


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class DiagramUpdateService:
    """Async orchestration with persistence and collaboration kept injectable."""

    def __init__(
        self,
        adapter: DiagramAdapter,
        *,
        load: Callable[[str], Any],
        save: Callable[[str, dict[str, Any]], Any],
        broadcast: Callable[[str, dict[str, Any]], Any],
        lock_for: Callable[[str], asyncio.Lock] | None = None,
    ) -> None:
        self.adapter = adapter
        self.load = load
        self.save = save
        self.broadcast = broadcast
        self._locks: dict[str, asyncio.Lock] = {}
        self.lock_for = lock_for or (lambda board_id: self._locks.setdefault(board_id, asyncio.Lock()))

    async def apply(
        self,
        board_id: str,
        mutations: list[dict[str, Any]],
        *,
        page_id: str | None = None,
    ) -> dict[str, Any]:
        async with self.lock_for(board_id):
            current = await _await(self.load(board_id))
            diagram = current.get("diagram") if isinstance(current, dict) and "diagram" in current else current
            updated = self.adapter.apply(diagram, mutations, page_id=page_id)
            await _await(self.save(board_id, updated))
            page = self.adapter._page(updated, page_id)
            await _await(self.broadcast(board_id, {
                "t": "state",
                "diagram": {"nodes": page.get("nodes", []), "edges": page.get("edges", [])},
                "from": 0,
                "pageId": page.get("id") if "pages" in updated else page_id,
                "removedNodeIds": [],
                "removedEdgeIds": [],
            }))
            return updated
