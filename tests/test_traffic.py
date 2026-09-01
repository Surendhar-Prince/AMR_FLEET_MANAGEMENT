import time
import networkx as nx
import pytest

from backend.amr import AMR
from backend.traffic.reservation import TrafficManager, Reservation


@pytest.fixture
def test_graph():
    graph = nx.DiGraph()
    graph.add_node("n1", x=0.0, y=0.0)
    graph.add_node("n2", x=5.0, y=0.0)
    graph.add_node("n3", x=10.0, y=0.0)
    graph.add_node("siding", x=5.0, y=5.0)

    for u, v in [("n1", "n2"), ("n2", "n3"), ("n2", "siding"), ("siding", "n2")]:
        graph.add_edge(u, v, weight=5.0)
        graph.add_edge(v, u, weight=5.0)
    return graph


def test_space_time_reservation_and_ghost_path_ttl(test_graph):
    tm = TrafficManager(ttl_buffer_seconds=1.0)
    now = 100.0

    # Agent 1 reserves n1 -> n2
    success1 = tm.reserve_path("amr-1", ["n1", "n2"], start_time=now, speed=1.0, graph=test_graph)
    assert success1 is True
    assert len(tm.reservations) > 0

    # Agent 2 tries to reserve conflicting n2 -> n1 at the same time -> should fail
    success2 = tm.reserve_path("amr-2", ["n2", "n1"], start_time=now, speed=1.0, graph=test_graph)
    assert success2 is False

    # Ghost Path Invalidation: Advance time past TTL
    tm.purge_expired(current_time=now + 100.0)
    assert len(tm.reservations) == 0

    # Now Agent 2 can reserve freely
    success3 = tm.reserve_path("amr-2", ["n2", "n1"], start_time=now + 100.0, speed=1.0, graph=test_graph)
    assert success3 is True


def test_deadlock_priority_and_evacuation(test_graph):
    tm = TrafficManager()
    amr_high = AMR(id="amr-high", current_node="n1", x=0.0, y=0.0, priority=5, path=["n2", "n3"])
    amr_low = AMR(id="amr-low", current_node="n2", x=5.0, y=0.0, priority=1, path=[])

    winner, yielder = tm.resolve_head_on(amr_high, amr_low, test_graph)
    assert winner == "amr-high"
    assert yielder == "amr-low"

    # Idle low-priority AMR at n2 evacuates to siding
    evac_node = tm.find_evacuation_node(test_graph, "n2", forbidden_nodes={"n1", "n3"})
    assert evac_node == "siding"


def test_hop_by_hop_lookahead_holding_and_advance_evacuation(test_graph):
    from backend.simulation import Simulation
    sim = Simulation(
        graph=test_graph,
        amr_configs=[
            {"id": "amr-2", "start_node": "n1"},
            {"id": "amr-4", "start_node": "n2"},
        ],
        speed=1.0,
        width=0.5,
        length=0.5,
    )
    # amr-2 ordered to go n1 -> n2 -> n3
    sim.set_order("amr-2", "n3")

    # On first step: amr-4 is at n2, so amr-4 is triggered to evacuate to 'siding', and amr-2 holds at n1!
    sim.step(1.0)
    assert sim.amrs["amr-4"].path == ["siding"]
    assert sim.amrs["amr-2"].current_node == "n1"  # amr-2 safely holds at preceding node n1!

    # Advance steps until amr-4 reaches siding and clears n2
    for _ in range(10):
        sim.step(1.0)

    # Now n2 is clear, amr-2 advances smoothly to n3!
    assert sim.amrs["amr-4"].current_node == "siding"
    assert sim.amrs["amr-2"].current_node in ("n2", "n3")


def test_car_following_same_edge_no_collision(test_graph):
    """Verify that a trailing AMR maintains safe gap behind a leading AMR on the same edge without colliding."""
    from backend.simulation import Simulation
    sim = Simulation(
        graph=test_graph,
        amr_configs=[
            {"id": "amr-1", "start_node": "n1"},
            {"id": "amr-2", "start_node": "n1"},
        ],
        speed=1.0,
        width=0.8,
        length=1.2,
    )
    sim.amrs["amr-2"].progress = 2.0  # amr-2 is ahead
    sim.amrs["amr-2"].path = ["n2"]
    sim.amrs["amr-1"].progress = 0.0  # amr-1 is trailing
    sim.amrs["amr-1"].path = ["n2"]
    sim._update_position(sim.amrs["amr-1"])
    sim._update_position(sim.amrs["amr-2"])

    collisions = 0
    for _ in range(40):
        sim.step(0.1)
        if sim.amrs["amr-1"].colliding or sim.amrs["amr-2"].colliding:
            collisions += 1

    assert collisions == 0, f"Collisions occurred during same-edge car following: {collisions}"


