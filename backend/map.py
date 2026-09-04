import json
import math
from typing import Callable, Optional

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
        graph.add_node(
            node["id"],
            x=node["x"],
            y=node["y"],
            type=node.get("type", "aisle"),
        )

    for edge in data["edges"]:
        from_node = graph.nodes[edge["from"]]
        to_node = graph.nodes[edge["to"]]
        distance = math.dist(
            (from_node["x"], from_node["y"]), (to_node["x"], to_node["y"])
        )
        graph.add_edge(edge["from"], edge["to"], weight=distance)

    return graph


def euclidean_heuristic(graph: nx.DiGraph, u: str, v: str) -> float:
    """Calculate Euclidean distance heuristic between two graph nodes."""
    node_u = graph.nodes[u]
    node_v = graph.nodes[v]
    return math.dist((node_u["x"], node_u["y"]), (node_v["x"], node_v["y"]))


def astar_path(
    graph: nx.DiGraph,
    source: str,
    target: str,
    dynamic_penalties: Optional[dict[tuple[str, str], float]] = None,
    blocked_nodes: Optional[set[str]] = None,
) -> list[str]:
    """Return the optimal A* path from source to target using Euclidean heuristic.

    Args:
        graph: The map graph.
        source: Starting node id.
        target: Destination node id.
        dynamic_penalties: Optional dictionary mapping (u, v) edge pairs to extra cost penalties.
        blocked_nodes: Optional set of node IDs that cannot be traversed (unless it's source or target).

    Returns:
        List of node ids from source to target, inclusive.
    """
    if source == target:
        return [source]

    def heuristic(u: str, v: str) -> float:
        return euclidean_heuristic(graph, u, v)

    def weight_func(u: str, v: str, edge_data: dict) -> float:
        if blocked_nodes and (u in blocked_nodes and u != source):
            return float("inf")
        if blocked_nodes and (v in blocked_nodes and v != target):
            return float("inf")
        base_weight = edge_data.get("weight", 1.0)
        penalty = 0.0
        if dynamic_penalties:
            penalty = dynamic_penalties.get((u, v), 0.0)
        return base_weight + penalty

    return nx.astar_path(
        graph,
        source,
        target,
        heuristic=heuristic,
        weight=weight_func,
    )


def shortest_path(graph: nx.DiGraph, source: str, target: str) -> list[str]:
    """Return the shortest node path from source to target using A*.

    Args:
        graph: The map graph.
        source: Starting node id.
        target: Destination node id.

    Returns:
        List of node ids from source to target, inclusive.
    """
    return astar_path(graph, source, target)


def path_length(graph: nx.DiGraph, path: list[str]) -> float:
    """Calculate the total Euclidean distance along a sequence of nodes."""
    if len(path) < 2:
        return 0.0
    total = 0.0
    for u, v in zip(path[:-1], path[1:]):
        total += graph.edges[u, v].get("weight", math.dist(
            (graph.nodes[u]["x"], graph.nodes[u]["y"]),
            (graph.nodes[v]["x"], graph.nodes[v]["y"])
        ))
    return total


def generate_procedural_map(variation_scale: float = 0.3) -> nx.DiGraph:
    """Generate the authentic 14-node warehouse topology with dynamic coordinate variations and full cross-corridors."""
    import random

    base_nodes = [
        {"id": "n1", "x": 0.0, "y": 0.0, "type": "dock"},
        {"id": "n2", "x": 3.5, "y": 0.0, "type": "aisle"},
        {"id": "n3", "x": 7.0, "y": 0.0, "type": "aisle"},
        {"id": "n4", "x": 10.5, "y": 0.0, "type": "aisle"},
        {"id": "n5", "x": 13.5, "y": 2.5, "type": "charging"},  # Corner Bay 3
        {"id": "n6", "x": 10.5, "y": 5.5, "type": "aisle"},
        {"id": "n7", "x": 7.0, "y": 5.5, "type": "aisle"},
        {"id": "n8", "x": 3.5, "y": 5.5, "type": "aisle"},
        {"id": "n9", "x": 0.0, "y": 5.5, "type": "dock"},
        {"id": "n10", "x": 0.0, "y": 9.0, "type": "charging"},  # Corner Bay 2
        {"id": "n11", "x": 3.5, "y": 9.0, "type": "aisle"},
        {"id": "n12", "x": 7.0, "y": 9.0, "type": "aisle"},
        {"id": "n13", "x": 10.5, "y": 9.0, "type": "aisle"},
        {"id": "n14", "x": 13.5, "y": 9.0, "type": "charging"}, # Corner Bay 1
    ]

    base_corridors = [
        ("n1", "n2"), ("n2", "n3"), ("n3", "n4"), ("n4", "n5"),
        ("n5", "n6"), ("n6", "n7"), ("n7", "n8"), ("n8", "n9"),
        ("n9", "n10"), ("n10", "n11"), ("n11", "n12"), ("n12", "n13"),
        ("n13", "n14"), ("n14", "n5"),
        # Authentic Cross-Aisle and Diagonal Shortcuts
        ("n1", "n9"), ("n3", "n7"), ("n6", "n12"), ("n8", "n11"),
        ("n10", "n13"),
    ]

    graph = nx.DiGraph()
    for n in base_nodes:
        jx = round(random.uniform(-variation_scale, variation_scale), 2)
        jy = round(random.uniform(-variation_scale, variation_scale), 2)
        graph.add_node(
            n["id"],
            x=round(n["x"] + jx, 2),
            y=round(n["y"] + jy, 2),
            type=n["type"],
        )

    for u, v in base_corridors:
        d = math.dist(
            (graph.nodes[u]["x"], graph.nodes[u]["y"]),
            (graph.nodes[v]["x"], graph.nodes[v]["y"]),
        )
        graph.add_edge(u, v, weight=round(d, 2))
        graph.add_edge(v, u, weight=round(d, 2))

    return graph

