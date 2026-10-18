"""Deterministic typed-architecture to AI Meeting Draw semantic mapping."""
from __future__ import annotations
import hashlib
import logging
from typing import Any
from .architecture import ArchitectureGraph, ArchitectureStatus, EntityType, Provenance, RelationshipType

logger=logging.getLogger("ai_meeting_draw.meeting.architecture_diagram")

NODE_KIND={
 EntityType.person:"actor",EntityType.client:"display",EntityType.browser_app:"display",EntityType.mobile_app:"display",EntityType.frontend:"display",
 EntityType.backend_service:"component",EntityType.microservice:"component",EntityType.api_gateway:"hexagon",EntityType.load_balancer:"trapezoid",
 EntityType.database:"cylinder",EntityType.cache:"internalStorage",EntityType.object_storage:"storedData",EntityType.queue:"queue",EntityType.topic:"queue",
 EntityType.message_broker:"component",EntityType.external_system:"cloud",EntityType.third_party_api:"cloud",EntityType.boundary:"package",
 EntityType.unknown_component:"rect",
}

def _id(prefix:str,semantic_id:str)->str:return f"{prefix}_{hashlib.sha256(semantic_id.encode()).hexdigest()[:16]}"

def _visual(provenance:Provenance,status:ArchitectureStatus)->dict[str,Any]:
    if status==ArchitectureStatus.contradicted:return {"stroke":"#dc2626","fill":"#fef2f2","dash":"dashed","opacity":1}
    if status in {ArchitectureStatus.retracted,ArchitectureStatus.superseded}:return {"stroke":"#94a3b8","fill":"#f8fafc","dash":"dashed","opacity":.45}
    if status==ArchitectureStatus.questioned or provenance==Provenance.questioned:return {"stroke":"#dc2626","fill":"#fff1f2","dash":"dashed","opacity":1}
    if provenance in {Provenance.defaulted,Provenance.inferred,Provenance.suggested}:return {"stroke":"#64748b","fill":"#f1f5f9","dash":"dotted","opacity":1}
    return {"stroke":"#334155","fill":"#ffffff","dash":"solid","opacity":1}

class ArchitectureDiagramMapper:
    def render(self,graph:ArchitectureGraph,existing:dict[str,Any]|None=None)->dict[str,Any]:
        existing=existing or {"nodes":[],"edges":[]}; old_nodes={n["id"]:n for n in existing.get("nodes",[])};old_edges={e["id"]:e for e in existing.get("edges",[])}
        logger.debug("Rendering architecture graph version=%d entities=%d relationships=%d questions=%d existing_nodes=%d existing_edges=%d",
                     graph.version,len(graph.entities),len(graph.relationships),len(graph.questions),len(old_nodes),len(old_edges))
        nodes=[]
        for entity in graph.entities.values():
            identifier=_id("arch_n",entity.id); prior=old_nodes.get(identifier,{})
            style=_visual(entity.provenance,entity.status)
            node={"id":identifier,"kind":NODE_KIND[entity.type],"x":prior.get("x",0),"y":prior.get("y",0),"w":prior.get("w",190),"h":prior.get("h",90),
                  "text":entity.name,"fill":style["fill"],"stroke":style["stroke"],"strokeWidth":2,"dash":style["dash"],"opacity":style["opacity"],
                  "architectureId":entity.id,"architectureType":entity.type.value,"provenance":entity.provenance.value,"status":entity.status.value}
            if "groupId" in prior:node["groupId"]=prior["groupId"]
            nodes.append(node)
        for question in graph.questions.values():
            identifier=_id("arch_q",question.id);prior=old_nodes.get(identifier,{})
            nodes.append({"id":identifier,"kind":"callout","x":prior.get("x",0),"y":prior.get("y",0),"w":prior.get("w",220),"h":prior.get("h",100),
                "text":question.text,"fill":"#fff1f2" if question.status=="open" else "#f0fdf4","stroke":"#dc2626" if question.status=="open" else "#16a34a",
                "strokeWidth":2,"dash":"dashed" if question.status=="open" else "solid","architectureId":question.id,"architectureType":"question"})
        edges=[]
        for rel in graph.relationships.values():
            identifier=_id("arch_e",rel.id);prior=old_edges.get(identifier,{})
            style=_visual(rel.provenance,rel.status);async_edge=rel.type in {RelationshipType.asynchronous_publish,RelationshipType.asynchronous_consume,RelationshipType.event_flow}
            label=rel.intent+(f" — {rel.protocol}" if rel.protocol else "")+(" (default)" if rel.provenance==Provenance.defaulted else "")
            edges.append({"id":identifier,"source":{"nodeId":_id("arch_n",rel.source_id),"kind":"floating"},"target":{"nodeId":_id("arch_n",rel.target_id),"kind":"floating"},
                "routing":prior.get("routing","elbow"),"head":"arrow","dash":"dashed" if async_edge else style["dash"],"stroke":style["stroke"],"strokeWidth":2,
                "label":label,"architectureId":rel.id,"relationshipType":rel.type.value,"provenance":rel.provenance.value,"status":rel.status.value})
        logger.info("Architecture graph mapped graph_version=%d nodes=%d edges=%d",graph.version,len(nodes),len(edges))
        return {"nodes":nodes,"edges":edges}
