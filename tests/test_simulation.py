import networkx as nx

from backend.simulation import Simulation


def straight_line_graph():
    graph = nx.DiGraph()
    graph.add_node("a", x=0.0, y=0.0)
    graph.add_node("b", x=10.0, y=0.0)
    graph.add_edge("a", "b", weight=10.0)
    return graph


def test_amr_starts_at_configured_node_position():
    sim = Simulation(
        graph=straight_line_graph(),
        amr_configs=[{"id": "amr-1", "start_node": "a"}],
        speed=1.0,
        width=0.5,
        length=0.5,
    )

    state = sim.snapshot()[0]

    assert state["position"] == {"x": 0.0, "y": 0.0}
    assert state["path"] == []


def test_amr_advances_along_edge_after_order_and_steps():
    sim = Simulation(
        graph=straight_line_graph(),
        amr_configs=[{"id": "amr-1", "start_node": "a"}],
        speed=2.0,
        width=0.5,
        length=0.5,
    )

    sim.set_order("amr-1", "b")
    sim.step(1.0)  # speed 2.0 * dt 1.0 = 2 meters along a 10 meter edge

    state = sim.snapshot()[0]

    assert state["position"]["x"] == 2.0
    assert state["position"]["y"] == 0.0
    assert state["path"] == ["b"]


def test_amr_reaches_target_node_after_enough_steps():
    sim = Simulation(
        graph=straight_line_graph(),
        amr_configs=[{"id": "amr-1", "start_node": "a"}],
        speed=2.0,
        width=0.5,
        length=0.5,
    )

    sim.set_order("amr-1", "b")
    for _ in range(10):
        sim.step(1.0)  # 10 steps * 2.0 m/s = 20 meters, edge is 10 meters

    state = sim.snapshot()[0]

    assert state["position"] == {"x": 10.0, "y": 0.0}
    assert state["path"] == []


def test_overlapping_amrs_flagged_colliding():
    sim = Simulation(
        graph=straight_line_graph(),
        amr_configs=[
            {"id": "amr-1", "start_node": "a"},
            {"id": "amr-2", "start_node": "a"},
        ],
        speed=1.0,
        width=0.5,
        length=0.5,
    )

    sim.step(0.0)

    states = {s["id"]: s for s in sim.snapshot()}
    assert states["amr-1"]["colliding"] is True
    assert states["amr-2"]["colliding"] is True


def test_far_apart_amrs_not_colliding():
    sim = Simulation(
        graph=straight_line_graph(),
        amr_configs=[
            {"id": "amr-1", "start_node": "a"},
            {"id": "amr-2", "start_node": "b"},
        ],
        speed=1.0,
        width=0.5,
        length=0.5,
    )

    sim.step(0.0)

    states = {s["id"]: s for s in sim.snapshot()}
    assert states["amr-1"]["colliding"] is False
    assert states["amr-2"]["colliding"] is False


def test_amr_waits_when_next_node_is_occupied():
    graph = nx.DiGraph()
    graph.add_node("a", x=0.0, y=0.0)
    graph.add_node("b", x=10.0, y=0.0)
    graph.add_edge("a", "b", weight=10.0)

    sim = Simulation(
        graph=graph,
        amr_configs=[
            {"id": "amr-1", "start_node": "b"},
            {"id": "amr-2", "start_node": "a"},
        ],
        speed=1.0,
        width=0.5,
        length=0.5,
    )

    sim.set_order("amr-2", "b")
    sim.step(1.0)

    states = {s["id"]: s for s in sim.snapshot()}
    assert states["amr-2"]["position"] == {"x": 0.0, "y": 0.0}
    assert states["amr-2"]["path"] == ["b"]


def test_amr_keeps_pending_order_queue():
    graph = nx.DiGraph()
    graph.add_node("a", x=0.0, y=0.0)
    graph.add_node("b", x=10.0, y=0.0)
    graph.add_node("c", x=20.0, y=0.0)
    graph.add_edge("a", "b", weight=10.0)
    graph.add_edge("b", "c", weight=10.0)

    sim = Simulation(
        graph=graph,
        amr_configs=[{"id": "amr-1", "start_node": "a"}],
        speed=10.0,
        width=0.5,
        length=0.5,
    )

    sim.set_order("amr-1", "b")
    sim.set_order("amr-1", "c")

    state = sim.snapshot()[0]
    assert state["path"] == ["b"]
    assert state["queued_targets"] == ["c"]
