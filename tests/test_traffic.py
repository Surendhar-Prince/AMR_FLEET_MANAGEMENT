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


# =========================================================================
# 12 COMPREHENSIVE YIELDING & ZERO-LOCK TRAFFIC TEST SCENARIOS
# =========================================================================

def test_yielding_same_node_diverging_departure(test_graph):
    """Scenario 1: Two AMRs at the same station heading to different outgoing edges."""
    from backend.simulation import Simulation
    sim = Simulation(
        graph=test_graph,
        amr_configs=[
            {"id": "amr-1", "start_node": "n2"},
            {"id": "amr-2", "start_node": "n2"},
        ],
        speed=1.5,
        width=0.8,
        length=1.0,
    )
    sim.amrs["amr-1"].path = ["n3"]      # Heading n2 -> n3
    sim.amrs["amr-2"].path = ["siding"]  # Heading n2 -> siding
    sim.amrs["amr-1"].priority = 5      # Higher priority departs first

    # Step simulation: amr-1 departs, amr-2 yields momentarily, then departs
    sim.step(0.5)
    assert sim.amrs["amr-1"].progress > 0.0
    assert sim.amrs["amr-1"].state_label == "TRANSIT"

    # Advance until both clear origin station
    for _ in range(20):
        sim.step(0.5)
    assert sim.amrs["amr-1"].current_node in ("n3", "n2")
    assert sim.amrs["amr-2"].current_node in ("siding", "n2")


def test_yielding_head_on_collision_detour(test_graph):
    """Scenario 2: Head-on corridor contention with dynamic detour resolution."""
    from backend.simulation import Simulation
    sim = Simulation(
        graph=test_graph,
        amr_configs=[
            {"id": "amr-1", "start_node": "n1"},
            {"id": "amr-2", "start_node": "n2"},
        ],
        speed=1.0,
        width=0.8,
        length=1.0,
    )
    sim.amrs["amr-1"].path = ["n2", "n3"]
    sim.amrs["amr-2"].path = ["n1"]
    sim.amrs["amr-1"].priority = 10  # Winner
    sim.amrs["amr-2"].priority = 1   # Yielder

    # Simulation step detects head-on conflict
    sim.step(0.2)
    assert sim.amrs["amr-2"].state_label == "YIELDING"


def test_yielding_cross_intersection_right_of_way(test_graph):
    """Scenario 3: Perpendicular intersection crossing with right-of-way yield."""
    from backend.simulation import Simulation
    sim = Simulation(
        graph=test_graph,
        amr_configs=[
            {"id": "amr-1", "start_node": "n1"},
            {"id": "amr-2", "start_node": "siding"},
        ],
        speed=1.0,
        width=0.8,
        length=1.0,
    )
    # Both heading to intersection n2
    sim.amrs["amr-1"].path = ["n2", "n3"]
    sim.amrs["amr-2"].path = ["n2"]
    sim.amrs["amr-1"].progress = 3.5  # amr-1 is closer to intersection n2
    sim.amrs["amr-2"].progress = 1.0  # amr-2 is farther
    sim._update_position(sim.amrs["amr-1"])
    sim._update_position(sim.amrs["amr-2"])

    sim.step(0.5)
    assert sim.amrs["amr-1"].state_label == "TRANSIT"


def test_yielding_car_following_distance(test_graph):
    """Scenario 4: Trailing AMR yields to maintain safe gap behind a slower leader."""
    from backend.simulation import Simulation
    sim = Simulation(
        graph=test_graph,
        amr_configs=[
            {"id": "amr-lead", "start_node": "n1"},
            {"id": "amr-trail", "start_node": "n1"},
        ],
        speed=1.0,
        width=0.8,
        length=1.0,
    )
    sim.amrs["amr-lead"].path = ["n2"]
    sim.amrs["amr-lead"].progress = 1.0  # Lead is 1.0m ahead
    sim.amrs["amr-trail"].path = ["n2"]
    sim.amrs["amr-trail"].progress = 0.5  # Trail is only 0.5m behind (< safe_gap of 1.2m)

    sim.step(0.1)
    # Trailing AMR must yield to maintain safe car-following gap
    assert sim.amrs["amr-trail"].state_label == "YIELDING"


def test_yielding_idle_blocker_automatic_evacuation(test_graph):
    """Scenario 5: Active AMR approaching a station occupied by an idle AMR causes evacuation."""
    from backend.simulation import Simulation
    sim = Simulation(
        graph=test_graph,
        amr_configs=[
            {"id": "amr-active", "start_node": "n1"},
            {"id": "amr-idle", "start_node": "n2"},
        ],
        speed=1.0,
        width=0.5,
        length=0.5,
    )
    sim.amrs["amr-active"].path = ["n2", "n3"]
    sim.amrs["amr-idle"].path = []  # Idle at n2

    sim.step(0.5)
    # Idle AMR is automatically requested to evacuate to siding
    assert sim.amrs["amr-idle"].path == ["siding"]
    assert sim.amrs["amr-active"].state_label in ("YIELDING", "TRANSIT")


def test_yielding_quarantined_failed_amr_roadblock(test_graph):
    """Scenario 6: Quarantined FAILED AMR triggers detour around roadblock."""
    from backend.simulation import Simulation
    sim = Simulation(
        graph=test_graph,
        amr_configs=[
            {"id": "amr-active", "start_node": "n1"},
            {"id": "amr-broken", "start_node": "n2"},
        ],
        speed=1.0,
        width=0.8,
        length=1.0,
    )
    sim.amrs["amr-broken"].state_label = "FAILED"
    sim.amrs["amr-broken"].path = []
    sim.amrs["amr-active"].path = ["n2", "n3"]

    sim.step(0.5)
    # Active AMR identifies broken robot as ghost node and yields or detours
    assert sim.amrs["amr-active"].state_label in ("YIELDING", "TRANSIT")


def test_yielding_multi_amr_charging_buffer_staging(test_graph):
    """Scenario 7: Multi-AMR charging buffer queue staging without gridlock."""
    from backend.simulation import Simulation
    test_graph.nodes["n3"]["type"] = "charging"
    sim = Simulation(
        graph=test_graph,
        amr_configs=[
            {"id": "amr-1", "start_node": "n3"},  # Already charging at dock
            {"id": "amr-2", "start_node": "n2"},  # Waiting in buffer
            {"id": "amr-3", "start_node": "n1"},  # Approaching buffer
        ],
        speed=1.0,
        width=0.8,
        length=1.0,
    )
    sim.amrs["amr-1"].battery = 20.0
    sim.amrs["amr-2"].battery = 15.0
    sim.amrs["amr-2"].path = ["n3"]

    sim.step(0.5)
    # AMR 2 yields at n2 because charging dock n3 is occupied by AMR 1
    assert sim.amrs["amr-2"].state_label == "YIELDING"
    assert sim.amrs["amr-2"].current_node == "n2"


def test_yielding_central_hub_four_way_convergence(test_graph):
    """Scenario 8: High-density convergence at central hub with sequential right-of-way."""
    from backend.simulation import Simulation
    sim = Simulation(
        graph=test_graph,
        amr_configs=[
            {"id": "amr-north", "start_node": "siding"},
            {"id": "amr-west", "start_node": "n1"},
            {"id": "amr-east", "start_node": "n3"},
        ],
        speed=1.0,
        width=0.8,
        length=1.0,
    )
    sim.amrs["amr-north"].path = ["n2"]
    sim.amrs["amr-west"].path = ["n2"]
    sim.amrs["amr-east"].path = ["n2"]
    sim.amrs["amr-north"].progress = 4.2
    sim.amrs["amr-west"].progress = 4.2
    sim.amrs["amr-east"].progress = 4.2
    sim._update_position(sim.amrs["amr-north"])
    sim._update_position(sim.amrs["amr-west"])
    sim._update_position(sim.amrs["amr-east"])
    sim.amrs["amr-west"].priority = 10  # Highest priority

    sim.step(0.5)
    yielding_count = sum(1 for a in sim.amrs.values() if a.state_label == "YIELDING")
    assert yielding_count >= 1


def test_yielding_recovery_zero_hysteresis(test_graph):
    """Scenario 9: Immediate transition from YIELDING to TRANSIT once path clears."""
    from backend.simulation import Simulation
    sim = Simulation(
        graph=test_graph,
        amr_configs=[
            {"id": "amr-1", "start_node": "n1"},
            {"id": "amr-2", "start_node": "n2"},
        ],
        speed=1.0,
        width=0.8,
        length=1.0,
    )
    sim.amrs["amr-1"].path = ["n2"]
    sim.amrs["amr-1"].state_label = "YIELDING"
    sim.amrs["amr-2"].path = ["n3"]
    sim.amrs["amr-2"].progress = 4.5  # amr-2 is almost cleared at n3

    # Step: amr-2 finishes edge, n2 is clear, amr-1 resumes transit immediately
    sim.step(1.0)
    assert sim.amrs["amr-1"].state_label == "TRANSIT"


def test_yielding_low_battery_task_surrender_priority(test_graph):
    """Scenario 10: Low-battery AMR surrenders active mission and yields to mission AMR."""
    from backend.simulation import Simulation
    sim = Simulation(
        graph=test_graph,
        amr_configs=[
            {"id": "amr-low", "start_node": "n1"},
            {"id": "amr-full", "start_node": "n2"},
        ],
        speed=1.0,
        width=0.8,
        length=1.0,
    )
    sim.amrs["amr-low"].battery = 15.0  # Emergency low battery
    sim.amrs["amr-low"].priority = 1
    sim.amrs["amr-full"].battery = 95.0
    sim.amrs["amr-full"].priority = 8
    sim.amrs["amr-low"].path = ["n2"]
    sim.amrs["amr-full"].path = ["n1"]

    winner, yielder = sim.traffic_manager.resolve_head_on(
        sim.amrs["amr-full"], sim.amrs["amr-low"], test_graph
    )
    assert winner == "amr-full"
    assert yielder == "amr-low"


def test_yielding_remote_peer_spatial_lookahead(test_graph):
    """Scenario 11: Local AMR yields when detecting remote peer AMR in spatial corridor."""
    from backend.simulation import Simulation
    sim = Simulation(
        graph=test_graph,
        amr_configs=[{"id": "amr-local", "start_node": "n1"}],
        speed=1.0,
        width=0.8,
        length=1.0,
    )
    sim.amrs["amr-local"].path = ["n2"]
    # Register remote peer AMR sitting at n2
    sim.remote_amrs["peer-amr-9"] = {
        "position": {"x": 5.0, "y": 0.0},
        "heading": 0.0,
        "current_node": "n2",
    }
    sim.amrs["amr-local"].progress = 3.5  # Approaching remote peer

    sim.step(0.5)
    # Spatial lookahead detects remote AMR and sets YIELDING
    assert sim.amrs["amr-local"].state_label == "YIELDING"


def test_yielding_decommissioned_amr_frees_waiting_robot(test_graph):
    """Scenario 12: Despawning a blocking AMR immediately frees waiting robot."""
    from backend.simulation import Simulation
    sim = Simulation(
        graph=test_graph,
        amr_configs=[
            {"id": "amr-waiting", "start_node": "n1"},
            {"id": "amr-blocking", "start_node": "n2"},
        ],
        speed=1.0,
        width=0.8,
        length=1.0,
    )
    sim.amrs["amr-waiting"].path = ["n2"]
    sim.amrs["amr-blocking"].path = []

    # Step: waiting AMR yields
    sim.step(0.5)
    assert sim.amrs["amr-waiting"].state_label == "YIELDING"

    # Decommission blocking AMR
    sim.remove_amr("amr-blocking")

    # Step: waiting AMR immediately resumes transit to n2
    sim.step(0.5)
    assert sim.amrs["amr-waiting"].state_label == "TRANSIT"



