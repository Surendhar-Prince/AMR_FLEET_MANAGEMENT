import networkx as nx
import pytest

from backend.cbba.engine import CBBAEngine
from backend.cbba.models import Task, TaskStatus


@pytest.fixture
def test_graph():
    graph = nx.DiGraph()
    # 4-node diamond: n1(0,0), n2(5,0), n3(10,0), n4(5,5)
    graph.add_node("n1", x=0.0, y=0.0)
    graph.add_node("n2", x=5.0, y=0.0)
    graph.add_node("n3", x=10.0, y=0.0)
    graph.add_node("n4", x=5.0, y=5.0)

    # bidirectional edges
    for u, v in [("n1", "n2"), ("n2", "n3"), ("n1", "n4"), ("n4", "n3")]:
        d = nx.utils.pairwise
        graph.add_edge(u, v, weight=5.0)
        graph.add_edge(v, u, weight=5.0)
    return graph


def test_cbba_phase1_single_robot(test_graph):
    engine = CBBAEngine(agent_id="amr-1", graph=test_graph, max_bundle_size=2)
    tasks = {
        "t1": Task(id="t1", pickup_node="n1", dropoff_node="n2", priority=1),
        "t2": Task(id="t2", pickup_node="n2", dropoff_node="n3", priority=2),
    }

    changed = engine.phase1_build_bundle(tasks, current_node="n1")
    assert changed is True
    # Priority 2 task should have higher bid
    assert len(engine.state.bundle) == 2
    assert "t1" in engine.state.bundle
    assert "t2" in engine.state.bundle


def test_cbba_phase2_multi_robot_consensus(test_graph):
    engine_1 = CBBAEngine(agent_id="amr-1", graph=test_graph, max_bundle_size=2)
    engine_2 = CBBAEngine(agent_id="amr-2", graph=test_graph, max_bundle_size=2)

    # Task close to AMR 1 (n1)
    # Task close to AMR 2 (n3)
    tasks = {
        "t_near_1": Task(id="t_near_1", pickup_node="n1", dropoff_node="n2", priority=1),
        "t_near_2": Task(id="t_near_2", pickup_node="n3", dropoff_node="n2", priority=1),
    }

    # AMR 1 builds bundle starting at n1
    engine_1.phase1_build_bundle(tasks, current_node="n1")
    # AMR 2 builds bundle starting at n3
    engine_2.phase1_build_bundle(tasks, current_node="n3")

    # Gossip exchange (Phase 2 consensus)
    engine_1.phase2_consensus(engine_2.state, tasks)
    engine_2.phase2_consensus(engine_1.state, tasks)

    # Convergence check: AMR 1 gets t_near_1, AMR 2 gets t_near_2
    assert engine_1.state.winning_agents["t_near_1"] == "amr-1"
    assert engine_2.state.winning_agents["t_near_2"] == "amr-2"
    # Zero double assignment
    assert set(engine_1.state.bundle).isdisjoint(set(engine_2.state.bundle))


def test_cbba_outbid_trimming(test_graph):
    engine = CBBAEngine(agent_id="amr-1", graph=test_graph, max_bundle_size=2)
    tasks = {
        "t1": Task(id="t1", pickup_node="n1", dropoff_node="n2", priority=1),
    }
    engine.phase1_build_bundle(tasks, current_node="n1")
    assert "t1" in engine.state.bundle

    # Simulate neighbor outbidding with higher bid
    neighbor_engine = CBBAEngine(agent_id="amr-2", graph=test_graph)
    neighbor_engine.state.winning_agents["t1"] = "amr-2"
    neighbor_engine.state.winning_bids["t1"] = 999.0

    engine.phase2_consensus(neighbor_engine.state, tasks)

    # AMR 1 must have released t1 from its bundle
    assert "t1" not in engine.state.bundle
    assert engine.state.winning_agents["t1"] == "amr-2"
