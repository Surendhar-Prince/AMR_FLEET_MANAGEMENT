import math
import time
from dataclasses import dataclass
from typing import Optional
import networkx as nx

from backend.map import astar_path


@dataclass
class Reservation:
    agent_id: str
    resource: str  # e.g., "node:n1" or "edge:n1->n2"
    start_time: float
    end_time: float
    priority: int = 1


class TrafficManager:
    """Manages space-time corridor reservations, prevents deadlocks, and resolves yielding."""

    def __init__(self, ttl_buffer_seconds: float = 2.0):
        self.reservations: list[Reservation] = []
        self.ttl_buffer_seconds = ttl_buffer_seconds

    def purge_expired(self, current_time: Optional[float] = None) -> None:
        """Purge expired space-time leases (Ghost Path Invalidation)."""
        now = current_time if current_time is not None else time.time()
        self.reservations = [r for r in self.reservations if r.end_time > now]

    def release_agent(self, agent_id: str) -> None:
        """Instantly release all corridor reservations for an agent."""
        self.reservations = [r for r in self.reservations if r.agent_id != agent_id]

    def reserve_path(
        self,
        agent_id: str,
        path: list[str],
        start_time: float,
        speed: float,
        graph: nx.DiGraph,
        priority: int = 1,
    ) -> bool:
        """Attempt to reserve space-time windows for a planned path."""
        self.purge_expired(start_time)
        self.release_agent(agent_id)

        if not path or len(path) < 2:
            return True

        new_reservations: list[Reservation] = []
        t = start_time

        for u, v in zip(path[:-1], path[1:]):
            edge_data = graph.edges.get((u, v), {})
            edge_len = edge_data.get("weight", math.dist(
                (graph.nodes[u]["x"], graph.nodes[u]["y"]),
                (graph.nodes[v]["x"], graph.nodes[v]["y"])
            ))
            duration = (edge_len / speed) if speed > 0 else 1.0
            t_end = t + duration + self.ttl_buffer_seconds

            node_res = f"node:{v}"
            edge_res = f"edge:{u}->{v}"
            opp_edge_res = f"edge:{v}->{u}"

            for r in self.reservations:
                if r.agent_id != agent_id:
                    if r.resource in (node_res, edge_res, opp_edge_res):
                        if not (t_end <= r.start_time or t >= r.end_time):
                            return False

            new_reservations.append(Reservation(agent_id, node_res, t, t_end, priority))
            new_reservations.append(Reservation(agent_id, edge_res, t, t_end, priority))
            t += duration

        self.reservations.extend(new_reservations)
        return True

    def find_evacuation_node(
        self,
        graph: nx.DiGraph,
        current_node: str,
        forbidden_nodes: set[str],
        occupied_nodes: Optional[set[str]] = None,
    ) -> Optional[str]:
        """Find an adjacent safe, empty siding node for an idle AMR to step aside."""
        forbidden = set(forbidden_nodes)
        if occupied_nodes:
            forbidden.update(occupied_nodes)

        neighbors = list(graph.neighbors(current_node))
        for neighbor in neighbors:
            if neighbor not in forbidden:
                return neighbor
        return None

    def resolve_head_on(
        self,
        amr_a,
        amr_b,
        graph: nx.DiGraph,
    ) -> tuple[str, str]:
        """Determine Right-of-Way between two opposing AMRs.

        When both AMRs are in YIELDING state and have identical priority,
        resolves right-of-way strictly via FIFO queue order (earliest yield start
        time / arrival timestamp), preventing mutual deadlock.
        """
        p_a = getattr(amr_a, "priority", 1) * 1000 + (100 if getattr(amr_a, "path", []) else 0)
        p_b = getattr(amr_b, "priority", 1) * 1000 + (100 if getattr(amr_b, "path", []) else 0)

        # Queue-based resolution: if both are yielding with the exact same priority
        st_a = getattr(amr_a, "state_label", "")
        st_b = getattr(amr_b, "state_label", "")
        if st_a == "YIELDING" and st_b == "YIELDING" and p_a == p_b:
            time_a = getattr(amr_a, "yield_start_time", 0.0)
            time_b = getattr(amr_b, "yield_start_time", 0.0)
            if time_a > 0 and time_b > 0 and time_a != time_b:
                if time_a < time_b:  # amr_a arrived earlier in queue -> gets right of way!
                    return amr_a.id, amr_b.id
                else:
                    return amr_b.id, amr_a.id

        if p_a > p_b:
            return amr_a.id, amr_b.id
        elif p_b > p_a:
            return amr_b.id, amr_a.id
        else:
            if amr_a.id < amr_b.id:
                return amr_a.id, amr_b.id
            return amr_b.id, amr_a.id

    def calculate_detour(
        self,
        graph: nx.DiGraph,
        source: str,
        target: str,
        blocked_nodes: set[str],
    ) -> Optional[list[str]]:
        """Calculate alternate A* detour avoiding blocked nodes."""
        try:
            return astar_path(graph, source, target, blocked_nodes=blocked_nodes)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
