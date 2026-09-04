import time
from typing import Optional
import networkx as nx

from backend.cbba.engine import CBBAEngine
from backend.cbba.models import ConsensusState, Task, TaskStatus
from backend.map import astar_path
from backend.nlp.translator import FleetNLPTranslator


class ParasiteNode:
    """Decentralized companion computer (Parasite Node) mounted on each AMR."""

    def __init__(
        self,
        agent_id: str,
        graph: nx.DiGraph,
        initial_battery: float = 100.0,
    ):
        self.agent_id = agent_id
        self.graph = graph
        self.battery_soc = initial_battery
        self.is_alive = True
        self.last_heartbeat = time.time()
        self.state_label = "IDLE"  # IDLE, BIDDING, TRANSIT, YIELDING, FAILED
        self.cbba = CBBAEngine(agent_id=agent_id, graph=graph)
        self.active_task_id: Optional[str] = None
        self.active_subtask: Optional[str] = None  # "PICKUP" or "DROPOFF"

    def tick_heartbeat(self) -> None:
        """Update heartbeat timestamp if node is alive."""
        if self.is_alive:
            self.last_heartbeat = time.time()

    def tick_cbba_phase1(
        self,
        tasks: dict[str, Task],
        current_node: str,
        occupied_nodes: Optional[set[str]] = None,
        failed_nodes: Optional[set[str]] = None,
    ) -> bool:
        """Execute CBBA Phase 1 Bundle Building with A* Congestion & Failed Dock Awareness."""
        if not self.is_alive:
            return False

        changed = self.cbba.phase1_build_bundle(
            tasks=tasks,
            current_node=current_node,
            battery_soc=self.battery_soc,
            occupied_nodes=occupied_nodes,
            failed_nodes=failed_nodes,
        )
        if changed:
            self.state_label = "BIDDING"
        return changed

    def receive_peer_consensus(self, peer_state: ConsensusState, tasks: dict[str, Task]) -> bool:
        """Execute CBBA Phase 2 Consensus Conflict Resolution with a peer."""
        if not self.is_alive:
            return False

        changed = self.cbba.phase2_consensus(peer_state, tasks)
        return changed

    def negotiate_p2p_traffic(self, peer_node: "ParasiteNode", amr_self, amr_peer, graph: nx.DiGraph) -> dict:
        """Explicit peer-to-peer right-of-way negotiation protocol between two parasite edge nodes."""
        p_self = getattr(amr_self, "priority", 1) * 1000 + (100 if amr_self.path else 0)
        p_peer = getattr(amr_peer, "priority", 1) * 1000 + (100 if amr_peer.path else 0)

        # Determine winner
        if p_self > p_peer:
            winner, yielder = amr_self.id, amr_peer.id
        elif p_peer > p_self:
            winner, yielder = amr_peer.id, amr_self.id
        else:
            winner = amr_self.id if amr_self.id < amr_peer.id else amr_peer.id
            yielder = amr_peer.id if winner == amr_self.id else amr_self.id

        corridor_desc = f"{amr_self.current_node}➔{amr_self.path[0] if amr_self.path else 'end'}"
        nlp_trans = FleetNLPTranslator.translate_p2p_yield(
            source=self.agent_id,
            target=peer_node.agent_id,
            winner=winner,
            yielder=yielder,
            corridor=corridor_desc,
        )

        return {
            "time": time.strftime("%H:%M:%S"),
            "source": self.agent_id,
            "target": peer_node.agent_id,
            "winner": winner,
            "yielder": yielder,
            "corridor": corridor_desc,
            "machine_protocol": nlp_trans["machine_protocol"],
            "human_speech": nlp_trans["human_speech"],
            "message": f"[{self.agent_id} ➔ {peer_node.agent_id}] \"{nlp_trans['human_speech']}\"",
        }

    def get_next_waypoint(self, tasks: dict[str, Task], current_node: str) -> Optional[str]:
        """Determine next station destination from won CBBA bundle with strict consensus verification."""
        if not self.is_alive or not self.cbba.state.bundle:
            self.active_task_id = None
            self.active_subtask = None
            if self.state_label != "FAILED":
                self.state_label = "IDLE"
            return None

        # Clean bundle: drop any task that this node did NOT win in consensus
        while self.cbba.state.bundle:
            candidate_task_id = self.cbba.state.bundle[0]
            if candidate_task_id not in tasks:
                self.cbba.state.bundle.pop(0)
                continue

            winner = self.cbba.state.winning_agents.get(candidate_task_id)
            if winner != self.agent_id:
                # We did NOT win this task in consensus! Drop it and remain parked
                self.cbba.state.bundle.pop(0)
                continue

            task = tasks[candidate_task_id]
            if task.status == TaskStatus.COMPLETED or (task.assigned_to and task.assigned_to != self.agent_id):
                self.cbba.state.bundle.pop(0)
                continue

            break

        if not self.cbba.state.bundle:
            self.active_task_id = None
            self.active_subtask = None
            if self.state_label != "FAILED":
                self.state_label = "IDLE"
            return None

        task_id = self.cbba.state.bundle[0]
        task = tasks[task_id]

        if self.active_task_id != task_id:
            self.active_task_id = task_id
            if self.active_subtask != "DROPOFF":
                self.active_subtask = "PICKUP"
            task.status = TaskStatus.IN_PROGRESS
            task.assigned_to = self.agent_id

        if self.active_subtask == "PICKUP":
            if current_node == task.pickup_node:
                self.active_subtask = "DROPOFF"
                return task.dropoff_node
            return task.pickup_node
        elif self.active_subtask == "DROPOFF":
            # HARD ONE-WAY STATE MACHINE LOCK:
            # Once cargo is loaded at pickup, the robot CAN NEVER return to PICKUP.
            # Its ONLY valid target until completion is task.dropoff_node!
            if current_node == task.dropoff_node:
                # Task Completed!
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
                self.cbba.release_task(task_id, tasks)
                self.active_task_id = None
                self.active_subtask = None
                return None
            return task.dropoff_node

        return None

    def drain_battery(
        self,
        distance_traveled: float,
        dt: float = 0.05,
        speed: float = 1.5,
        has_payload: bool = False,
    ) -> None:
        """Simulate realistic battery discharge based on kinetic motion, payload mass, and idle avionics."""
        if self.is_alive and self.state_label != "CHARGING":
            idle_drain = 0.025 * dt  # Continuous LiDAR, computing, and avionics draw
            payload_mult = 1.6 if has_payload else 1.0  # Increased torque for active cargo payload
            motion_drain = distance_traveled * 0.38 * payload_mult
            comms_drain = 0.005 if self.state_label == "BIDDING" else 0.0
            total_discharge = idle_drain + motion_drain + comms_drain
            self.battery_soc = max(1.0, round(self.battery_soc - total_discharge, 2))

    def recharge(self, dt: float) -> None:
        """Recharge battery when parked at charging station."""
        if self.is_alive and self.battery_soc < 100.0:
            self.battery_soc = min(100.0, round(self.battery_soc + 12.0 * dt, 1))

    def kill(self, tasks: dict[str, Task]) -> None:
        """Simulate hardware failure (Chaos Engineering)."""
        self.is_alive = False
        self.state_label = "FAILED"
        for task_id in list(self.cbba.state.bundle):
            if task_id in tasks:
                tasks[task_id].status = TaskStatus.UNASSIGNED
                tasks[task_id].assigned_to = None
            self.cbba.release_task(task_id, tasks)

    def recover(self) -> None:
        """Recover from simulated failure."""
        self.is_alive = True
        self.state_label = "IDLE"
        self.last_heartbeat = time.time()

    def snapshot(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "is_alive": self.is_alive,
            "battery_soc": self.battery_soc,
            "state_label": self.state_label,
            "active_task_id": self.active_task_id,
            "active_subtask": self.active_subtask,
            "bundle": list(self.cbba.state.bundle),
            "consensus": self.cbba.state.to_dict(),
        }
