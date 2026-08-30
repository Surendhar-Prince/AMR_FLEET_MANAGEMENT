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

    @classmethod
    def at_node(cls, amr_id: str, node_id: str, graph: nx.DiGraph) -> "AMR":
        node = graph.nodes[node_id]
        return cls(id=amr_id, current_node=node_id, x=node["x"], y=node["y"])
