from dataclasses import dataclass, field

import networkx as nx


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
    queued_targets: list[str] = field(default_factory=list)

    @classmethod
    def at_node(cls, amr_id: str, node_id: str, graph: nx.DiGraph) -> "AMR":
        node = graph.nodes[node_id]
        return cls(id=amr_id, current_node=node_id, x=node["x"], y=node["y"])

    def enqueue_target(self, target: str) -> None:
        self.queued_targets.append(target)

    def start_next_target_if_idle(self, graph: nx.DiGraph) -> None:
        if self.path:
            return
        if not self.queued_targets:
            return

        target = self.queued_targets.pop(0)
        from backend.map import shortest_path

        route = shortest_path(graph, self.current_node, target)
        self.path = route[1:]
        self.progress = 0.0
