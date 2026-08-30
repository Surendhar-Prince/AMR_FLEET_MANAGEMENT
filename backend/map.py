import json
import math

import networkx as nx


def load_map(path: str) -> nx.DiGraph:
    """Load a simple node/edge JSON map into a directed graph.

    Args:
        path: Path to a JSON file with "nodes" ({"id", "x", "y"}) and
            "edges" ({"from", "to"}) lists.

    Returns:
        A networkx.DiGraph with node attributes x, y and edge attribute
        weight (Euclidean distance between endpoints).
    """
    with open(path) as f:
        data = json.load(f)

    graph = nx.DiGraph()
    for node in data["nodes"]:
        graph.add_node(node["id"], x=node["x"], y=node["y"])

    for edge in data["edges"]:
        from_node = graph.nodes[edge["from"]]
        to_node = graph.nodes[edge["to"]]
        distance = math.dist(
            (from_node["x"], from_node["y"]), (to_node["x"], to_node["y"])
        )
        graph.add_edge(edge["from"], edge["to"], weight=distance)

    return graph


def shortest_path(graph: nx.DiGraph, source: str, target: str) -> list[str]:
    """Return the shortest node path from source to target.

    Args:
        graph: The map graph.
        source: Starting node id.
        target: Destination node id.

    Returns:
        List of node ids from source to target, inclusive.
    """
    return nx.shortest_path(graph, source, target, weight="weight")
