import networkx as nx
import pytest

from backend.cbba.models import Task, TaskStatus
from backend.simulation import Simulation


@pytest.fixture
def warehouse_graph():
    """A realistic 6-node warehouse graph with interconnected aisles."""
    graph = nx.DiGraph()
    # Nodes:
    # n1(0,0) --- n2(5,0) --- n3(10,0)
    #   |           |           |
    # n4(0,5) --- n5(5,5) --- n6(10,5)
    coords = {
        "n1": (0.0, 0.0), "n2": (5.0, 0.0), "n3": (10.0, 0.0),
        "n4": (0.0, 5.0), "n5": (5.0, 5.0), "n6": (10.0, 5.0),
    }
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y)

    # Bidirectional edges
    edges = [
        ("n1", "n2"), ("n2", "n3"),
        ("n4", "n5"), ("n5", "n6"),
        ("n1", "n4"), ("n2", "n5"), ("n3", "n6"),
    ]
    for u, v in edges:
        graph.add_edge(u, v, weight=5.0)
        graph.add_edge(v, u, weight=5.0)

    return graph


def test_scenario_1_single_amr(warehouse_graph):
    """Scenario 1: 1 AMR autonomously claims, executes, and completes a task."""
    sim = Simulation(
        graph=warehouse_graph,
        amr_configs=[{"id": "amr-1", "start_node": "n1"}],
        speed=5.0,  # 5 m/s for fast test simulation
        width=0.6,
        length=0.8,
    )

    # 1. Add task: Pickup at n2, Dropoff at n3
    task = sim.add_task("task-1", pickup_node="n2", dropoff_node="n3", priority=1)

    # 2. Step simulation: AMR-1 bids on task-1 and claims it
    sim.step(0.1)
    assert "task-1" in sim.amrs["amr-1"].parasite.cbba.state.bundle
    assert task.status == TaskStatus.IN_PROGRESS
    assert task.assigned_to == "amr-1"

    # 3. Simulate until task is completed (n1 -> n2 -> n3 = 10m / 5m/s = 2s)
    for _ in range(30):
        sim.step(0.1)

    assert task.status == TaskStatus.COMPLETED
    assert task.completed_at is not None
    assert sim.amrs["amr-1"].current_node == "n3"
    assert sim.amrs["amr-1"].state_label == "IDLE"


def test_scenario_2_two_amrs(warehouse_graph):
    """Scenario 2: 2 AMRs bid on 2 different tasks and execute in parallel without conflict."""
    sim = Simulation(
        graph=warehouse_graph,
        amr_configs=[
            {"id": "amr-1", "start_node": "n1"},
            {"id": "amr-2", "start_node": "n6"},
        ],
        speed=5.0,
        width=0.6,
        length=0.8,
    )

    # Task A is near AMR-1 (n1 -> n2)
    # Task B is near AMR-2 (n6 -> n5)
    task_a = sim.add_task("task-A", pickup_node="n1", dropoff_node="n2", priority=1)
    task_b = sim.add_task("task-B", pickup_node="n6", dropoff_node="n5", priority=1)

    # Step simulation for CBBA bidding & consensus
    sim.step(0.1)

    # AMR-1 must win Task A, AMR-2 must win Task B
    assert "task-A" in sim.amrs["amr-1"].parasite.cbba.state.bundle
    assert "task-B" in sim.amrs["amr-2"].parasite.cbba.state.bundle
    assert task_a.assigned_to == "amr-1"
    assert task_b.assigned_to == "amr-2"

    # Step simulation to completion
    for _ in range(30):
        sim.step(0.1)

    assert task_a.status == TaskStatus.COMPLETED
    assert task_b.status == TaskStatus.COMPLETED
    assert sim.amrs["amr-1"].current_node == "n2"
    assert sim.amrs["amr-2"].current_node == "n5"


def test_scenario_3_three_amrs(warehouse_graph):
    """Scenario 3: 3 AMRs bid on 3 warehouse tasks across different zones."""
    sim = Simulation(
        graph=warehouse_graph,
        amr_configs=[
            {"id": "amr-1", "start_node": "n1"},
            {"id": "amr-2", "start_node": "n3"},
            {"id": "amr-3", "start_node": "n4"},
        ],
        speed=5.0,
        width=0.6,
        length=0.8,
    )

    # 3 distinct tasks
    task_1 = sim.add_task("task-zone-1", pickup_node="n1", dropoff_node="n2", priority=1)
    task_2 = sim.add_task("task-zone-2", pickup_node="n3", dropoff_node="n6", priority=1)
    task_3 = sim.add_task("task-zone-3", pickup_node="n4", dropoff_node="n5", priority=1)

    # CBBA Bidding & Consensus
    sim.step(0.1)

    # Verify 3-way conflict-free consensus
    assert "task-zone-1" in sim.amrs["amr-1"].parasite.cbba.state.bundle
    assert "task-zone-2" in sim.amrs["amr-2"].parasite.cbba.state.bundle
    assert "task-zone-3" in sim.amrs["amr-3"].parasite.cbba.state.bundle

    assert task_1.assigned_to == "amr-1"
    assert task_2.assigned_to == "amr-2"
    assert task_3.assigned_to == "amr-3"

    # Advance all 3 robots in parallel to completion
    for _ in range(30):
        sim.step(0.1)

    assert task_1.status == TaskStatus.COMPLETED
    assert task_2.status == TaskStatus.COMPLETED
    assert task_3.status == TaskStatus.COMPLETED

    assert sim.amrs["amr-1"].current_node == "n2"
    assert sim.amrs["amr-2"].current_node == "n6"
    assert sim.amrs["amr-3"].current_node == "n5"


def test_ghost_node_bypass_single_pickup_and_smooth_delivery(warehouse_graph):
    """Verify that when a ghost/failed node blocks the direct aisle, an AMR executes

    the bypass once with zero repeat loops back to pickup.
    """
    sim = Simulation(
        graph=warehouse_graph,
        amr_configs=[
            {"id": "amr-1", "start_node": "n1"},
            {"id": "amr-3", "start_node": "n5"},
        ],
        speed=5.0,
        width=0.6,
        length=0.8,
    )

    # 1. Kill amr-1 at n1 (Ghost node at n1)
    sim.kill_node("amr-1")
    assert sim.amrs["amr-1"].state_label == "FAILED"

    # 2. Task from n4 to n2 (Direct path n4->n1->n2 is BLOCKED by failed amr-1 at n1)
    task = sim.add_task("task-bypass", pickup_node="n4", dropoff_node="n2", priority=1)

    # 3. amr-3 at n5 bids and claims task-bypass
    sim.step(0.1)
    assert task.assigned_to == "amr-3"

    # 4. Advance amr-3 and record visited nodes
    visited_nodes = [sim.amrs["amr-3"].current_node]
    for _ in range(40):
        sim.step(0.1)
        curr = sim.amrs["amr-3"].current_node
        if curr != visited_nodes[-1]:
            visited_nodes.append(curr)

    # Verify task completed
    assert task.status == TaskStatus.COMPLETED
    assert sim.amrs["amr-3"].current_node == "n2"

    # Verify that pickup node 'n4' was visited EXACTLY ONCE (No repeat loops!)
    n4_visit_count = visited_nodes.count("n4")
    assert n4_visit_count == 1, f"Expected n4 to be visited exactly once, but was visited {n4_visit_count} times: {visited_nodes}"

    # Verify that the ghost node 'n1' was NEVER visited
    assert "n1" not in visited_nodes, "Robot should NEVER visit the ghost node n1!"


def test_auto_simulation_continuous_stream_and_drain(warehouse_graph):
    """Verify that a continuous stream of auto-simulation tasks across the warehouse
    executes with zero collisions and completes cleanly.
    """
    sim = Simulation(
        graph=warehouse_graph,
        amr_configs=[
            {"id": "amr-1", "start_node": "n1"},
            {"id": "amr-2", "start_node": "n3"},
            {"id": "amr-3", "start_node": "n6"},
        ],
        speed=5.0,
        width=0.6,
        length=0.8,
    )

    task_pairs = [
        ("auto-1", "n1", "n3", 3),
        ("auto-2", "n4", "n6", 2),
        ("auto-3", "n2", "n5", 2),
        ("auto-4", "n6", "n1", 1),
    ]

    for tid, u, v, prio in task_pairs:
        sim.add_task(tid, pickup_node=u, dropoff_node=v, priority=prio)

    collisions = 0
    for _ in range(200):
        sim.step(0.1)
        for amr in sim.amrs.values():
            if amr.colliding:
                collisions += 1

    assert collisions == 0, f"Collisions occurred during auto-simulation: {collisions}"
    for tid, _, _, _ in task_pairs:
        assert sim.tasks[tid].status == TaskStatus.COMPLETED, f"Task {tid} failed to reach COMPLETED status: {sim.tasks[tid].status}"


