import networkx as nx
import pytest

from backend.cbba.models import Task, TaskStatus
from backend.simulation import Simulation


@pytest.fixture
def test_graph():
    graph = nx.DiGraph()
    graph.add_node("n1", x=0.0, y=0.0)
    graph.add_node("n2", x=5.0, y=0.0)
    graph.add_node("n3", x=10.0, y=0.0)

    for u, v in [("n1", "n2"), ("n2", "n3")]:
        graph.add_edge(u, v, weight=5.0)
        graph.add_edge(v, u, weight=5.0)
    return graph


def test_node_kill_and_autonomous_reauction(test_graph):
    sim = Simulation(
        graph=test_graph,
        amr_configs=[
            {"id": "amr-1", "start_node": "n1"},
            {"id": "amr-2", "start_node": "n3"},
        ],
        speed=1.0,
        width=0.6,
        length=0.8,
    )

    # Add task at n1
    task = sim.add_task("task-1", pickup_node="n1", dropoff_node="n2")

    # Step simulation to trigger CBBA
    sim.step(0.1)

    # amr-1 is at n1 so it wins task-1
    assert "task-1" in sim.amrs["amr-1"].parasite.cbba.state.bundle

    # Simulate hardware breakdown on amr-1
    sim.kill_node("amr-1")
    assert sim.amrs["amr-1"].state_label == "FAILED"
    assert sim.amrs["amr-1"].parasite.is_alive is False

    # Step simulation: surviving amr-2 must claim task-1
    sim.step(0.1)
    sim.step(0.1)

    assert "task-1" in sim.amrs["amr-2"].parasite.cbba.state.bundle
    assert sim.tasks["task-1"].assigned_to == "amr-2"
