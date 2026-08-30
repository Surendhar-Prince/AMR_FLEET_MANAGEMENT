from backend.map import load_map, shortest_path


def test_load_map_creates_nodes_with_coordinates():
    graph = load_map("maps/sample_map.json")

    assert graph.nodes["n1"]["x"] == 0.0
    assert graph.nodes["n1"]["y"] == 0.0
    assert graph.nodes["n2"]["x"] == 5.0
    assert graph.nodes["n2"]["y"] == 0.0


def test_load_map_creates_directed_edges():
    graph = load_map("maps/sample_map.json")

    assert graph.has_edge("n1", "n2")
    assert not graph.has_edge("n2", "n1")


def test_shortest_path_follows_edge_direction_around_loop():
    graph = load_map("maps/sample_map.json")

    path = shortest_path(graph, "n1", "n3")

    assert path == ["n1", "n2", "n3"]
