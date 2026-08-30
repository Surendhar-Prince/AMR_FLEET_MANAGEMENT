import math

import networkx as nx

from backend.amr import AMR
from backend.collision import rectangles_overlap
from backend.map import shortest_path


class Simulation:
    """Advances AMRs along graph edges and flags footprint collisions."""

    def __init__(
        self,
        graph: nx.DiGraph,
        amr_configs: list[dict],
        speed: float,
        width: float,
        length: float,
    ):
        self.graph = graph
        self.speed = speed
        self.width = width
        self.length = length
        self.amrs: dict[str, AMR] = {
            cfg["id"]: AMR.at_node(cfg["id"], cfg["start_node"], graph)
            for cfg in amr_configs
        }

    def set_order(self, amr_id: str, target_node: str) -> None:
        """Route an AMR to target_node via the shortest path from its current node."""
        amr = self.amrs[amr_id]
        path = shortest_path(self.graph, amr.current_node, target_node)
        amr.path = path[1:]
        amr.progress = 0.0

    def step(self, dt: float) -> None:
        for amr in self.amrs.values():
            self._advance(amr, dt)
        self._update_collisions()

    def _advance(self, amr: AMR, dt: float) -> None:
        remaining = self.speed * dt
        while remaining > 0 and amr.path:
            target_node = amr.path[0]
            edge_length = self.graph.edges[amr.current_node, target_node]["weight"]
            remaining_on_edge = edge_length - amr.progress
            if remaining < remaining_on_edge:
                amr.progress += remaining
                remaining = 0.0
            else:
                remaining -= remaining_on_edge
                amr.current_node = amr.path.pop(0)
                amr.progress = 0.0

        self._update_position(amr)

    def _update_position(self, amr: AMR) -> None:
        from_node = self.graph.nodes[amr.current_node]
        if amr.path:
            to_node = self.graph.nodes[amr.path[0]]
            edge_length = self.graph.edges[amr.current_node, amr.path[0]]["weight"]
            fraction = amr.progress / edge_length if edge_length else 0.0
            amr.x = from_node["x"] + (to_node["x"] - from_node["x"]) * fraction
            amr.y = from_node["y"] + (to_node["y"] - from_node["y"]) * fraction
            amr.heading = math.atan2(
                to_node["y"] - from_node["y"], to_node["x"] - from_node["x"]
            )
        else:
            amr.x = from_node["x"]
            amr.y = from_node["y"]

    def _update_collisions(self) -> None:
        amrs = list(self.amrs.values())
        for amr in amrs:
            amr.colliding = False
        for i in range(len(amrs)):
            for j in range(i + 1, len(amrs)):
                a, b = amrs[i], amrs[j]
                rect_a = (a.x, a.y, a.heading, self.width, self.length)
                rect_b = (b.x, b.y, b.heading, self.width, self.length)
                if rectangles_overlap(rect_a, rect_b):
                    a.colliding = True
                    b.colliding = True

    def snapshot(self) -> list[dict]:
        return [
            {
                "id": amr.id,
                "position": {"x": amr.x, "y": amr.y},
                "heading": amr.heading,
                "path": list(amr.path),
                "colliding": amr.colliding,
            }
            for amr in self.amrs.values()
        ]
