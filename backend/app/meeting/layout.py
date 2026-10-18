"""Stable lane-based incremental layout for live architecture diagrams."""
from __future__ import annotations
from collections import defaultdict
import logging
from typing import Any

logger=logging.getLogger("ai_meeting_draw.meeting.layout")

LANE={
 "person":0,"client":0,"browser_app":0,"mobile_app":0,"frontend":0,
 "api_gateway":1,"load_balancer":1,
 "backend_service":2,"microservice":2,"unknown_component":2,"boundary":2,
 "database":3,"cache":3,"object_storage":3,"queue":3,"topic":3,"message_broker":3,
 "external_system":4,"third_party_api":4,"question":5,
}

class IncrementalArchitectureLayout:
    def __init__(self,*,origin_x:float=80,origin_y:float=80,lane_gap:float=300,row_gap:float=140):
        self.origin_x=origin_x;self.origin_y=origin_y;self.lane_gap=lane_gap;self.row_gap=row_gap
    def apply(self,diagram:dict[str,Any],*,existing_node_ids:set[str]|None=None)->dict[str,Any]:
        existing_node_ids=existing_node_ids or set();result={**diagram,"nodes":[dict(node) for node in diagram.get("nodes",[])],"edges":[dict(edge) for edge in diagram.get("edges",[])]}
        occupied:dict[int,list[float]]=defaultdict(list)
        for node in result["nodes"]:
            lane=LANE.get(node.get("architectureType","unknown_component"),2)
            if node["id"] in existing_node_ids:occupied[lane].append(float(node.get("y",0)))
        counters:dict[int,int]=defaultdict(int)
        for node in sorted(result["nodes"],key=lambda item:(LANE.get(item.get("architectureType","unknown_component"),2),item.get("architectureId",item["id"]))):
            if node["id"] in existing_node_ids:continue
            lane=LANE.get(node.get("architectureType","unknown_component"),2);y=self.origin_y+counters[lane]*self.row_gap
            while any(abs(y-used)<self.row_gap*.8 for used in occupied[lane]):y+=self.row_gap
            node["x"]=self.origin_x+lane*self.lane_gap;node["y"]=y;occupied[lane].append(y);counters[lane]+=1
        logger.debug("Incremental layout complete nodes=%d preserved=%d newly_positioned=%d",
                     len(result["nodes"]),len(existing_node_ids),len(result["nodes"])-len(existing_node_ids & {n["id"] for n in result["nodes"]}))
        return result
