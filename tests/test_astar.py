import math
import networkx as nx
import pytest

from backend.map import astar_path, euclidean_heuristic, path_length, load_map


def test_euclidean_heuristic():
    graph = nx.DiGraph()
    graph.add_node("n1", x=0.0, y=0.0)
    graph.add_node("n2", x=3.0, y=4.0)
    h = euclidean_heuristic(graph, "n1", "n2")
    assert math.isclose(h, 5.0)


def test_astar_path_basic(tmp_path):
    map_file = tmp_path / "map.json"
    map_file.write_text("""{
        "nodes": [
            {"id": "n1", "x": 0.0, "y": 0.0},
            {"id": "n2", "x": 5.0, "y": 0.0},
            {"id": "n3", "x": 10.0, "y": 0.0}
        ],
        "edges": [
            {"from": "n1", "to": "n2"},
            {"from": "n2", "to": "n3"}
        ]
    }""")
    graph = load_map(str(map_file))
    path = astar_path(graph, "n1", "n3")
    assert path == ["n1", "n2", "n3"]
    assert math.isclose(path_length(graph, path), 10.0)


def test_astar_dynamic_detour(tmp_path):
    # n1 -> n2 -> n4 (length 2.0)
    # n1 -> n3 -> n4 (length 4.0)
    map_file = tmp_path / "map.json"
    map_file.write_text("""{
        "nodes": [
            {"id": "n1", "x": 0.0, "y": 0.0},
            {"id": "n2", "x": 1.0, "y": 0.0},
            {"id": "n3", "x": 0.0, "y": 2.0},
            {"id": "n4", "x": 2.0, "y": 0.0}
        ],
        "edges": [
            {"from": "n1", "to": "n2"},
            {"from": "n2", "to": "n4"},
            {"from": "n1", "to": "n3"},
            {"from": "n3", "to": "n4"}
        ]
    }""")
    graph = load_map(str(map_file))

    # Without blocked node: takes n1 -> n2 -> n4
    path1 = astar_path(graph, "n1", "n4")
    assert path1 == ["n1", "n2", "n4"]

    # When n2 is blocked: takes detour n1 -> n3 -> n4
    path2 = astar_path(graph, "n1", "n4", blocked_nodes={"n2"})
    assert path2 == ["n1", "n3", "n4"]
