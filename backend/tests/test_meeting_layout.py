from app.meeting.layout import IncrementalArchitectureLayout

def node(index,kind):return {"id":f"n{index}","architectureId":f"ent_{index}","architectureType":kind,"x":0,"y":0,"w":190,"h":90}

def test_layout_uses_architecture_lanes_and_has_no_lane_overlap():
    kinds=["person","frontend","api_gateway"]+["microservice"]*15+["database"]*10+["message_broker"]*5+["external_system"]*8
    result=IncrementalArchitectureLayout().apply({"nodes":[node(i,k) for i,k in enumerate(kinds)],"edges":[]})
    positions={(n["x"],n["y"]) for n in result["nodes"]}
    assert len(positions)==len(result["nodes"])
    assert next(n for n in result["nodes"] if n["architectureType"]=="person")["x"] < next(n for n in result["nodes"] if n["architectureType"]=="microservice")["x"]
    assert next(n for n in result["nodes"] if n["architectureType"]=="external_system")["x"] > next(n for n in result["nodes"] if n["architectureType"]=="database")["x"]

def test_existing_positions_are_never_moved():
    diagram={"nodes":[node(1,"microservice"),node(2,"database")],"edges":[]};diagram["nodes"][0].update(x=777,y=333)
    result=IncrementalArchitectureLayout().apply(diagram,existing_node_ids={"n1"})
    assert (result["nodes"][0]["x"],result["nodes"][0]["y"])==(777,333)
    assert (result["nodes"][1]["x"],result["nodes"][1]["y"])!=(0,0)
