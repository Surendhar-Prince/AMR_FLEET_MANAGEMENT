import networkx as nx
import pytest

from backend.beacon import UDPNetworkManager
from backend.cbba.models import Task, TaskStatus
from backend.simulation import Simulation


@pytest.fixture
def mesh_graph():
    graph = nx.DiGraph()
    coords = {
        "n1": (0.0, 0.0), "n2": (5.0, 0.0), "n3": (10.0, 0.0),
        "n4": (0.0, 5.0), "n5": (5.0, 5.0), "n6": (10.0, 5.0),
    }
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y)
    edges = [
        ("n1", "n2"), ("n2", "n3"),
        ("n4", "n5"), ("n5", "n6"),
        ("n1", "n4"), ("n2", "n5"), ("n3", "n6"),
    ]
    for u, v in edges:
        graph.add_edge(u, v, weight=5.0)
        graph.add_edge(v, u, weight=5.0)
    return graph


def test_udp_task_announce_sync(mesh_graph):
    """Test that a task announced on Simulation A is ingested into Simulation B's task pool."""
    sim_a = Simulation(
        graph=mesh_graph,
        amr_configs=[{"id": "alpha-1", "start_node": "n1"}],
        speed=1.0,
        width=0.6,
        length=0.8,
    )
    sim_b = Simulation(
        graph=mesh_graph,
        amr_configs=[{"id": "beta-1", "start_node": "n6"}],
        speed=1.0,
        width=0.6,
        length=0.8,
    )

    # 1. Sim A creates a task
    task = sim_a.add_task("task-sync-1", pickup_node="n2", dropoff_node="n5", priority=2, broadcast=False)

    # 2. Simulate UDP transmission of TASK_ANNOUNCE packet from Sim A to Sim B
    packet = {
        "type": "TASK_ANNOUNCE",
        "sender_host": "host-alpha",
        "task": task.to_dict(),
    }
    sim_b.handle_network_packet(packet)

    # 3. Verify Sim B now has the task in its local pool
    assert "task-sync-1" in sim_b.tasks
    assert sim_b.tasks["task-sync-1"].pickup_node == "n2"
    assert sim_b.tasks["task-sync-1"].dropoff_node == "n5"
    assert sim_b.tasks["task-sync-1"].priority == 2


def test_udp_cbba_cross_node_consensus(mesh_graph):
    """Test that cross-node CBBA gossip packets over UDP converge to the true highest bidder."""
    sim_a = Simulation(
        graph=mesh_graph,
        amr_configs=[{"id": "alpha-1", "start_node": "n1"}],
        speed=1.0,
        width=0.6,
        length=0.8,
    )
    sim_b = Simulation(
        graph=mesh_graph,
        amr_configs=[{"id": "beta-1", "start_node": "n3"}],
        speed=1.0,
        width=0.6,
        length=0.8,
    )

    # Task is near Sim A (n1 -> n2)
    sim_a.add_task("task-p2p", pickup_node="n1", dropoff_node="n2", priority=3, broadcast=False)
    sim_b.add_task("task-p2p", pickup_node="n1", dropoff_node="n2", priority=3, broadcast=False)

    # 1. Both nodes compute their local Phase 1 bids
    sim_a.step(0.1)
    sim_b.step(0.1)

    # alpha-1 is at n1, so alpha-1 bid is much higher than beta-1 (at n3)
    alpha_state = sim_a.amrs["alpha-1"].parasite.cbba.state
    beta_state = sim_b.amrs["beta-1"].parasite.cbba.state

    assert alpha_state.winning_bids.get("task-p2p", 0.0) > beta_state.winning_bids.get("task-p2p", 0.0)

    # 2. Transmit CBBA_GOSSIP packet from Sim A to Sim B
    gossip_packet = {
        "type": "CBBA_GOSSIP",
        "sender_host": "host-alpha",
        "agent_id": "alpha-1",
        "consensus": alpha_state.to_dict(),
    }
    sim_b.handle_network_packet(gossip_packet)

    # 3. Verify Sim B converged to recognize alpha-1 as the winner
    assert sim_b.amrs["beta-1"].parasite.cbba.state.winning_agents.get("task-p2p") == "alpha-1"


def test_remote_shadow_amr_obstacle_avoidance(mesh_graph):
    """Test that position beacons from a remote AMR register as obstacles in local A* routing."""
    sim_a = Simulation(
        graph=mesh_graph,
        amr_configs=[{"id": "alpha-1", "start_node": "n1"}],
        speed=1.0,
        width=0.6,
        length=0.8,
    )

    # 1. Sim A receives AMR_BEACON from Laptop B: beta-1 is operating at n2
    beacon_packet = {
        "type": "AMR_BEACON",
        "sender_host": "host-beta",
        "amr": {
            "id": "beta-1",
            "current_node": "n2",
            "position": {"x": 5.0, "y": 0.0},
            "heading": 0.0,
            "path": ["n3"],
            "state_label": "TRANSIT",
            "battery_soc": 95.0,
        },
    }
    sim_a.handle_network_packet(beacon_packet)

    # Verify beta-1 is tracked as a remote shadow AMR
    assert "beta-1" in sim_a.remote_amrs

    # Verify snapshot includes both local and remote AMRs
    snapshot = sim_a.snapshot()
    ids = {item["id"]: item["is_remote"] for item in snapshot}
    assert ids["alpha-1"] is False  # Local
    assert ids["beta-1"] is True   # Remote shadow

    # 2. Congestion graph on Sim A automatically penalizes the node occupied by beta-1
    c_graph = sim_a._build_congested_graph("alpha-1")
    assert c_graph.edges["n1", "n2"]["weight"] > mesh_graph.edges["n1", "n2"]["weight"]


def test_proactive_advance_siding_evacuation(mesh_graph):
    """Test that an idle local AMR proactively evacuates its station when a remote AMR is approaching."""
    sim_a = Simulation(
        graph=mesh_graph,
        amr_configs=[{"id": "alpha-1", "start_node": "n2"}],
        speed=1.0,
        width=0.6,
        length=0.8,
    )

    # 1. Sim A receives beacon showing remote beta-1 is targeting n2
    beacon_packet = {
        "type": "AMR_BEACON",
        "sender_host": "host-beta",
        "amr": {
            "id": "beta-1",
            "current_node": "n3",
            "position": {"x": 10.0, "y": 0.0},
            "heading": 0.0,
            "path": ["n2"],
            "state_label": "TRANSIT",
            "battery_soc": 90.0,
        },
    }
    sim_a.handle_network_packet(beacon_packet)

    # 2. Step simulation: alpha-1 (idle at n2) should proactively vacate n2 to a safe siding (n1 or n5)
    sim_a.step(0.1)

    alpha_amr = sim_a.amrs["alpha-1"]
    assert alpha_amr.path != []  # Has chosen a siding evacuation path
    assert alpha_amr.path[0] in ("n1", "n5")  # Moved to adjacent empty station
    assert alpha_amr.state_label == "TRANSIT"


def test_fifo_queue_equal_priority_resolution(mesh_graph):
    """Test that equal-priority yielding AMRs resolve right-of-way via FIFO queue arrival timestamps."""
    from backend.amr import AMR
    from backend.traffic.reservation import TrafficManager

    tm = TrafficManager()
    amr_a = AMR.at_node("alpha-1", "n1", mesh_graph)
    amr_b = AMR.at_node("beta-1", "n2", mesh_graph)
    amr_a.path = ["n2"]
    amr_b.path = ["n1"]
    amr_a.priority = 1
    amr_b.priority = 1
    amr_a.state_label = "YIELDING"
    amr_b.state_label = "YIELDING"

    # AMR A arrived at time 100.0, AMR B arrived at time 102.5
    amr_a.yield_start_time = 100.0
    amr_b.yield_start_time = 102.5

    winner_id, yielder_id = tm.resolve_head_on(amr_a, amr_b, mesh_graph)
    assert winner_id == "alpha-1"
    assert yielder_id == "beta-1"


def test_p2p_task_completion_broadcast_and_speech(mesh_graph):
    """Test that completing a task logs bilingual natural language dialogue and updates status."""
    sim = Simulation(
        graph=mesh_graph,
        amr_configs=[{"id": "alpha-1", "start_node": "n1"}],
        speed=10.0,  # Fast speed to finish delivery quickly
        width=0.6,
        length=0.8,
    )

    task = sim.add_task("task-comp-1", pickup_node="n1", dropoff_node="n2", priority=1, broadcast=False)
    # Step simulation until delivery arrives at dropoff n2
    for _ in range(20):
        sim.step(0.1)

    # Verify task completed
    assert task.status == TaskStatus.COMPLETED
    # Verify natural language dialogue recorded in P2P conversation feed
    dialogue_messages = [d.get("human_speech", "") for d in sim.p2p_conversations]
    assert any("Delivery confirmed" in msg or "dropped off" in msg for msg in dialogue_messages)

