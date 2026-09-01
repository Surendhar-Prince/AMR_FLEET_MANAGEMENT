from dataclasses import dataclass, field
from typing import Optional
import networkx as nx

from backend.parasite.node import ParasiteNode


@dataclass
class AMR:
    id: str
    current_node: str
    x: float
    y: float
    heading: float = 0.0
    path: list[str] = field(default_factory=list)
    progress: float = 0.0
    colliding: bool = False
    priority: int = 1
    state_label: str = "IDLE"  # IDLE, BIDDING, TRANSIT, YIELDING, FAILED
    queued_targets: list[str] = field(default_factory=list)
    parasite: Optional[ParasiteNode] = None
    yield_start_time: float = 0.0

    @classmethod
    def at_node(cls, amr_id: str, node_id: str, graph: nx.DiGraph, initial_battery: float = 100.0) -> "AMR":
        node = graph.nodes[node_id]
        parasite = ParasiteNode(agent_id=amr_id, graph=graph, initial_battery=initial_battery)
        return cls(
            id=amr_id,
            current_node=node_id,
            x=node["x"],
            y=node["y"],
            parasite=parasite,
        )

    def enqueue_target(self, target: str) -> None:
        self.queued_targets.append(target)

    def start_next_target_if_idle(self, graph: nx.DiGraph) -> None:
        if self.path:
            return
        if not self.queued_targets:
            return

        target = self.queued_targets.pop(0)
        from backend.map import astar_path

        route = astar_path(graph, self.current_node, target)
        self.path = route[1:]
        self.progress = 0.0
        self.state_label = "TRANSIT"
