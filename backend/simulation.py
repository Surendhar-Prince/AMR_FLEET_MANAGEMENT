import math
import threading
import time
from typing import Optional
import networkx as nx

from backend.amr import AMR
from backend.cbba.models import Task, TaskStatus
from backend.collision import rectangles_overlap
from backend.map import astar_path
from backend.traffic.reservation import TrafficManager


class Simulation:
    """Advances AMRs along graph edges, executes decentralized CBBA, and avoids deadlocks."""

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
        self.traffic_manager = TrafficManager(ttl_buffer_seconds=2.0)
        self.tasks: dict[str, Task] = {}
        self.task_history: list[dict] = []
        self.remote_amrs: dict[str, dict] = {}
        self.network_packets_count = 0
        self.network_manager = None
        self.last_p2p_events: list[dict] = []
        self.p2p_conversations: list[dict] = []
        # Protects dynamic mutations of self.amrs between the tick loop
        # (which iterates amrs) and the registration endpoint (which inserts).
        self._amrs_lock = threading.Lock()

        self.amrs: dict[str, AMR] = {
            cfg["id"]: AMR.at_node(
                amr_id=cfg["id"],
                node_id=cfg["start_node"],
                graph=graph,
                initial_battery=cfg.get("battery", 100.0),
            )
            for cfg in amr_configs
        }

    def get_charging_node(self) -> str:
        """Dynamically detect designated charging station node from the loaded map graph."""
        for n, d in self.graph.nodes(data=True):
            if d.get("type") == "charging" or "charge" in str(n).lower():
                return n
        if "n14" in self.graph.nodes:
            return "n14"
        return list(self.graph.nodes)[-1] if self.graph.nodes else "n1"

    def add_amr(self, amr_id: str, start_node: str) -> AMR:
        """Dynamically insert a new AMR into the running simulation.

        Called from the registration endpoint.  Thread-safe against the
        tick loop which reads self.amrs concurrently.

        Args:
            amr_id:     Unique identifier (e.g. "amr-001").
            start_node: A node id that exists in the loaded map graph.

        Returns:
            The newly created AMR instance.

        Raises:
            ValueError: If amr_id already exists or start_node is unknown.
        """
        if start_node not in self.graph.nodes:
            raise ValueError(f"Unknown start_node: {start_node!r}")
        with self._amrs_lock:
            if amr_id in self.amrs:
                raise ValueError(f"AMR id already exists: {amr_id!r}")
            amr = AMR.at_node(
                amr_id=amr_id,
                node_id=start_node,
                graph=self.graph,
            )
            # Replace the dict atomically so the tick loop always sees a
            # consistent snapshot (dict assignment is GIL-protected in CPython,
            # and the lock guards the broader check-then-insert sequence).
            new_amrs = dict(self.amrs)
            new_amrs[amr_id] = amr
            self.amrs = new_amrs
        return amr

    def remove_amr(self, amr_id: str) -> bool:
        """Dynamically remove an AMR from the running simulation and release its tasks.

        Args:
            amr_id: Unique identifier to remove.

        Returns:
            True if removed, False if not found.
        """
        with self._amrs_lock:
            if amr_id not in self.amrs:
                return False
            amr = self.amrs[amr_id]
            # Release any tasks held by this AMR back to the pool
            if amr.parasite:
                for task_id in list(amr.parasite.cbba.state.bundle):
                    if task_id in self.tasks:
                        self.tasks[task_id].status = TaskStatus.UNASSIGNED
                        self.tasks[task_id].assigned_to = None
                    amr.parasite.cbba.release_task(task_id, self.tasks)
                amr.parasite.active_task_id = None
                amr.parasite.active_subtask = None

            # Remove from space-time reservations
            if hasattr(self.traffic_manager, "reservation_table"):
                self.traffic_manager.reservation_table.cancel_agent(amr_id)

            new_amrs = dict(self.amrs)
            new_amrs.pop(amr_id, None)
            self.amrs = new_amrs
            return True

    def get_occupied_nodes(self) -> set[str]:
        """Return set of all nodes currently occupied by local or remote AMRs."""
        occupied = {a.current_node for a in self.amrs.values()}
        for r_amr in self.remote_amrs.values():
            if r_curr := r_amr.get("current_node"):
                occupied.add(r_curr)
        return occupied

    def get_charging_nodes(self) -> list[str]:
        """Return all stations tagged as charging bays in the graph."""
        nodes = [
            n for n, d in self.graph.nodes(data=True)
            if d.get("type") == "charging" or "charge" in str(n).lower()
        ]
        if not nodes:
            if "n14" in self.graph.nodes:
                nodes = ["n14"]
            elif self.graph.nodes:
                nodes = [list(self.graph.nodes)[-1]]
            else:
                nodes = ["n14"]
        return nodes

    def get_charging_node(self, from_node: Optional[str] = None) -> str:
        """Find the closest vacant or staged charging bay among all available chargers."""
        charging_bays = self.get_charging_nodes()
        if not charging_bays:
            return "n14"
        if not from_node:
            return charging_bays[0]

        occupied = self.get_occupied_nodes()
        vacant_bays = [b for b in charging_bays if b not in occupied]
        target_pool = vacant_bays if vacant_bays else charging_bays

        best_bay = target_pool[0]
        best_dist = float("inf")
        for bay in target_pool:
            try:
                p = astar_path(self.graph, from_node, bay)
                d = path_length(self.graph, p)
                if d < best_dist:
                    best_dist = d
                    best_bay = bay
            except Exception:
                pass
        return best_bay

    def add_task(
        self,
        task_id: str,
        pickup_node: str,
        dropoff_node: str,
        priority: int = 1,
        broadcast: bool = True,
    ) -> Task:
        """Add a warehouse task to the decentralized task pool and broadcast over UDP."""
        if pickup_node not in self.graph.nodes:
            raise ValueError(f"Unknown pickup node: {pickup_node}")
        if dropoff_node not in self.graph.nodes:
            raise ValueError(f"Unknown dropoff node: {dropoff_node}")

        task = Task(
            id=task_id,
            pickup_node=pickup_node,
            dropoff_node=dropoff_node,
            priority=priority,
            status=TaskStatus.UNASSIGNED,
        )
        self.tasks[task_id] = task

        # Add / update record in task_history
        self._record_task_history(task)

        if broadcast and self.network_manager:
            self.network_manager.broadcast_task_announce(task.to_dict())

        return task

    def _record_task_history(self, task: Task) -> None:
        """Upsert a task entry in the historical log with duration calculation."""
        t_dict = task.to_dict()
        duration = None
        if task.completed_at and task.created_at:
            duration = round(task.completed_at - task.created_at, 2)
        t_dict["duration_seconds"] = duration
        t_dict["formatted_time"] = time.strftime("%H:%M:%S", time.localtime(task.created_at))

        for idx, item in enumerate(self.task_history):
            if item.get("id") == task.id:
                self.task_history[idx] = t_dict
                return
        self.task_history.append(t_dict)
        # Keep maximum last 200 tasks in history
        if len(self.task_history) > 200:
            self.task_history.pop(0)

    def get_task_history(self) -> list[dict]:
        """Return chronological history of all dispatched, active, and completed tasks."""
        # Refresh current active tasks in history
        for task in self.tasks.values():
            self._record_task_history(task)
        return list(self.task_history)

    def clear_tasks(self, include_active: bool = False) -> int:
        """Clear completed or all tasks from the active pool and update history."""
        with self._amrs_lock:
            cleared = 0
            for tid in list(self.tasks.keys()):
                t = self.tasks[tid]
                if include_active or t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                    if include_active and t.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                        t.status = TaskStatus.FAILED
                    self._record_task_history(t)
                    # Release from all AMRs
                    for amr in self.amrs.values():
                        if amr.parasite:
                            if tid in amr.parasite.cbba.state.bundle:
                                amr.parasite.cbba.state.bundle = [
                                    b for b in amr.parasite.cbba.state.bundle if b != tid
                                ]
                            if amr.parasite.active_task_id == tid:
                                amr.parasite.active_task_id = None
                                amr.parasite.active_subtask = None
                                amr.path = []
                                amr.state_label = "IDLE"
                    self.tasks.pop(tid, None)
                    cleared += 1
            return cleared

    def set_order(self, amr_id: str, target_node: str) -> None:
        """Queue a direct target for an AMR (backwards-compatible)."""
        amr = self.amrs[amr_id]
        failed_nodes = {
            a.current_node
            for a in self.amrs.values()
            if a.id != amr_id and (a.state_label == "FAILED" or (a.parasite and not a.parasite.is_alive))
        }
        if not amr.path and not amr.queued_targets:
            path = astar_path(self.graph, amr.current_node, target_node, blocked_nodes=failed_nodes)
            amr.path = path[1:]
            amr.progress = 0.0
            amr.state_label = "TRANSIT"
            return

        amr.enqueue_target(target_node)

    def kill_node(self, amr_id: str) -> None:
        """Simulate hardware failure on a specific AMR."""
        if amr_id in self.amrs:
            amr = self.amrs[amr_id]
            last_known = amr.current_node
            amr.state_label = "FAILED"
            if amr.parasite:
                amr.parasite.kill(self.tasks)
            self.traffic_manager.release_agent(amr_id)

            # Generate and broadcast P2P Node Quarantine Gossip message
            from backend.nlp.translator import FleetNLPTranslator
            quarantine_msg = FleetNLPTranslator.translate_node_offline_quarantine(amr_id, last_known)
            self.p2p_conversations.append({
                "time": time.strftime("%H:%M:%S"),
                "source": amr_id,
                "target": "ALL_PEERS",
                "winner": "MESH_QUARANTINE",
                "yielder": amr_id,
                "corridor": f"Station {last_known} [BLOCKED]",
                "machine_protocol": quarantine_msg["machine_protocol"],
                "human_speech": quarantine_msg["human_speech"],
                "message": f"[{amr_id} ➔ ALL_PEERS] \"{quarantine_msg['human_speech']}\"",
            })
            self.p2p_conversations = self.p2p_conversations[-20:]

            # Unassign tasks won by this failed robot and clear paths targeting the failed station
            for other_amr in self.amrs.values():
                if other_amr.id != amr_id and other_amr.parasite:
                    for task_id in list(self.tasks.keys()):
                        if other_amr.parasite.cbba.state.winning_agents.get(task_id) == amr_id:
                            other_amr.parasite.cbba.state.winning_agents[task_id] = ""
                            other_amr.parasite.cbba.state.winning_bids[task_id] = 0.0

                    # If other AMR's upcoming path passes through the quarantined station, reroute dynamically!
                    if other_amr.path and last_known in other_amr.path:
                        try:
                            target = other_amr.path[-1]
                            detour = astar_path(self.graph, other_amr.current_node, target, blocked_nodes={last_known})
                            other_amr.path = detour[1:]
                            other_amr.progress = 0.0
                            other_amr.state_label = "TRANSIT"
                        except Exception:
                            pass

    def recover_node(self, amr_id: str) -> None:
        """Recover a failed AMR."""
        if amr_id in self.amrs:
            amr = self.amrs[amr_id]
            amr.state_label = "IDLE"
            if amr.parasite:
                amr.parasite.recover()

    def get_cbba_state(self) -> dict:
        """Return the current consensus state, comparative bid matrix, and network telemetry."""
        nodes_snapshot = {
            amr_id: amr.parasite.snapshot() if amr.parasite else {}
            for amr_id, amr in self.amrs.items()
        }

        # Collect occupied nodes and failed station nodes across the fleet
        occupied_nodes = {a.current_node for a in self.amrs.values()}
        failed_nodes = {
            a.current_node
            for a in self.amrs.values()
            if a.state_label == "FAILED" or (a.parasite and not a.parasite.is_alive)
        }

        bid_matrix = []
        for task_id, task in self.tasks.items():
            # Compute optimal A* delivery path for this task
            try:
                task_route = astar_path(self.graph, task.pickup_node, task.dropoff_node, blocked_nodes=failed_nodes)
            except Exception:
                task_route = [task.pickup_node, task.dropoff_node]

            is_blocked = (task.pickup_node in failed_nodes) or (task.dropoff_node in failed_nodes)
            task_status_display = "BLOCKED (Dock Disabled)" if is_blocked else task.status.value

            row = {
                "task_id": task.id,
                "pickup": task.pickup_node,
                "dropoff": task.dropoff_node,
                "priority": task.priority,
                "status": task_status_display,
                "assigned_to": task.assigned_to,
                "planned_route": task_route,
                "bids": {},
            }
            for amr_id, amr in self.amrs.items():
                if amr.parasite and amr.parasite.is_alive:
                    other_occupied = {n for n in occupied_nodes if n != amr.current_node}
                    calculated_bid = amr.parasite.cbba.calculate_bid(
                        task,
                        amr.current_node,
                        amr.parasite.battery_soc,
                        occupied_nodes=other_occupied,
                        failed_nodes=failed_nodes,
                    )
                    row["bids"][amr_id] = calculated_bid
                else:
                    row["bids"][amr_id] = 0.0
            bid_matrix.append(row)

        return {
            "tasks": [t.to_dict() for t in self.tasks.values()],
            "nodes": nodes_snapshot,
            "bid_matrix": bid_matrix,
            "network_telemetry": {
                "mesh_protocol": "UDP Broadcast / P2P Gossip (Port 9999)",
                "active_nodes_count": sum(1 for a in self.amrs.values() if a.parasite and a.parasite.is_alive),
                "total_packets_exchanged": self.network_packets_count,
                "latency_ms": 1.2,
                "recent_p2p_gossip": self.last_p2p_events[-6:],
                "p2p_dialogues": self.p2p_conversations[-8:],
            },
        }

    def handle_network_packet(self, packet: dict) -> None:
        """Process incoming UDP network packets from peer laptops across the mesh."""
        self.network_packets_count += 1
        p_type = packet.get("type")

        # 1. New task announced by peer laptop
        if p_type == "TASK_ANNOUNCE":
            task_dict = packet.get("task", {})
            tid = task_dict.get("id")
            if tid and tid not in self.tasks:
                self.tasks[tid] = Task.from_dict(task_dict)

        # 2. CBBA Consensus Gossip received from peer AMR
        elif p_type == "CBBA_GOSSIP":
            agent_id = packet.get("agent_id")
            consensus_dict = packet.get("consensus", {})
            if agent_id and consensus_dict:
                from backend.cbba.models import ConsensusState
                peer_state = ConsensusState.from_dict(consensus_dict)
                for amr in self.amrs.values():
                    if amr.parasite and amr.parasite.is_alive and amr.id != agent_id:
                        amr.parasite.receive_peer_consensus(peer_state, self.tasks)

        # 3. Real-time AMR Position Beacon received from peer laptop
        elif p_type == "AMR_BEACON":
            amr_dict = packet.get("amr", {})
            remote_id = amr_dict.get("id")
            if remote_id and remote_id not in self.amrs:
                prev = self.remote_amrs.get(remote_id)
                self.remote_amrs[remote_id] = amr_dict
                cur_node = amr_dict.get("current_node", "?")
                st = amr_dict.get("state_label", "IDLE")
                if not prev or prev.get("state_label") != st or prev.get("current_node") != cur_node:
                    msg = f"📡 {remote_id}: 'Operating at Station {cur_node} [{st}]. Mesh coordinates synchronized.'"
                    if msg not in self.p2p_conversations:
                        self.p2p_conversations.append(msg)
                        if len(self.p2p_conversations) > 20:
                            self.p2p_conversations.pop(0)

        # 4. Task Status updates from peer laptops
        elif p_type == "TASK_STATUS":
            tid = packet.get("task_id")
            status = packet.get("status")
            assigned_to = packet.get("assigned_to", "PEER")
            if tid in self.tasks and status:
                self.tasks[tid].status = TaskStatus(status)
                if assigned_to:
                    self.tasks[tid].assigned_to = assigned_to
                if status == "COMPLETED":
                    comp_node = self.tasks[tid].dropoff_node
                    from backend.nlp.translator import FleetNLPTranslator
                    nlp_trans = FleetNLPTranslator.translate_task_complete(
                        assigned_to, tid, comp_node
                    )
                    self.p2p_conversations.append({
                        "time": time.strftime("%H:%M:%S"),
                        "source": assigned_to,
                        "target": "ALL_PEERS",
                        "winner": assigned_to,
                        "yielder": "N/A",
                        "corridor": f"Station {comp_node} [VACANT]",
                        "machine_protocol": nlp_trans["machine_protocol"],
                        "human_speech": nlp_trans["human_speech"],
                        "message": f"[{assigned_to} ➔ ALL_PEERS] \"{nlp_trans['human_speech']}\"",
                    })
                    self.p2p_conversations = self.p2p_conversations[-20:]

    def _build_congested_graph(self, for_amr_id: str) -> nx.DiGraph:
        """Create a graph with dynamic weight penalties on nodes occupied or contested by local and remote AMRs."""
        congested = self.graph.copy()
        # Local AMRs
        for amr in self.amrs.values():
            if amr.id != for_amr_id and amr.state_label != "FAILED":
                occupied = [amr.current_node]
                if amr.path:
                    occupied.append(amr.path[0])
                for node in occupied:
                    if node in congested:
                        for u, v, d in congested.in_edges(node, data=True):
                            d["weight"] = d.get("weight", 1.0) + 25.0
                        for u, v, d in congested.out_edges(node, data=True):
                            d["weight"] = d.get("weight", 1.0) + 25.0

        # Remote Shadow AMRs from peer laptops
        for r_amr in self.remote_amrs.values():
            r_node = r_amr.get("current_node")
            r_path = r_amr.get("path", [])
            occupied = [r_node] if r_node else []
            if r_path:
                occupied.append(r_path[0])
            for node in occupied:
                if node in congested:
                    for u, v, d in congested.in_edges(node, data=True):
                        d["weight"] = d.get("weight", 1.0) + 25.0
                    for u, v, d in congested.out_edges(node, data=True):
                        d["weight"] = d.get("weight", 1.0) + 25.0

        return congested

    def step(self, dt: float) -> None:
        """Main simulation tick with autonomous decentralized ghost mode detection."""
        # 0. Autonomous Decentralized Ghost Mode (Battery Depletion & Yielding Stall Timeout)
        charging_bays = self.get_charging_nodes()
        for amr in list(self.amrs.values()):
            if amr.parasite and amr.parasite.is_alive:
                # Trigger A: Autonomous Critical Battery Exhaustion (<= 1.0%)
                if amr.parasite.battery_soc <= 1.0 and amr.current_node not in charging_bays and amr.state_label != "CHARGING":
                    self.kill_node(amr.id)
                    continue

                # Trigger B: Autonomous Obstacle Stall / Long Yielding Timeout (> 15.0s)
                if amr.state_label == "YIELDING" and amr.yield_start_time > 0:
                    if (time.time() - amr.yield_start_time) >= 15.0:
                        self.kill_node(amr.id)
                        continue

        # 1. Update heartbeats and purge stale ghost path leases
        self.traffic_manager.purge_expired()
        for amr in self.amrs.values():
            if amr.parasite:
                amr.parasite.tick_heartbeat()

        # 2. Decentralized CBBA: Phase 1 (Bundle Building on Edge Nodes with A* Congestion & Failed Dock Awareness)
        occupied_nodes = {a.current_node for a in self.amrs.values()}
        failed_nodes = {
            a.current_node
            for a in self.amrs.values()
            if a.state_label == "FAILED" or (a.parasite and not a.parasite.is_alive)
        }
        for amr in self.amrs.values():
            if amr.parasite and amr.parasite.is_alive:
                other_occupied = {n for n in occupied_nodes if n != amr.current_node}
                amr.parasite.tick_cbba_phase1(
                    self.tasks,
                    amr.current_node,
                    occupied_nodes=other_occupied,
                    failed_nodes=failed_nodes,
                )

        # 3. Decentralized CBBA: Phase 2 (P2P Mesh Consensus Gossip across Neighbors until Full Convergence)
        amr_list = [a for a in self.amrs.values() if a.parasite and a.parasite.is_alive]
        p2p_round_events = []

        # Iterate until full network consensus convergence across all peers (maximum diameter rounds)
        for _ in range(max(1, len(amr_list))):
            any_changed = False
            for i in range(len(amr_list)):
                for j in range(i + 1, len(amr_list)):
                    node_a = amr_list[i].parasite
                    node_b = amr_list[j].parasite
                    changed_a = node_a.receive_peer_consensus(node_b.cbba.state, self.tasks)
                    changed_b = node_b.receive_peer_consensus(node_a.cbba.state, self.tasks)
                    self.network_packets_count += 2
                    if changed_a or changed_b:
                        any_changed = True
                        p2p_round_events.append({
                            "pair": f"{node_a.agent_id} ⇄ {node_b.agent_id}",
                            "time": time.strftime("%H:%M:%S"),
                            "status": "Consensus Converged / Bids Updated",
                        })
            if not any_changed:
                break

        if p2p_round_events:
            self.last_p2p_events.extend(p2p_round_events)
            self.last_p2p_events = self.last_p2p_events[-20:]

        # 4. Synchronize task state with fleet consensus and strictly lock non-winners
        for task_id, task in self.tasks.items():
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                continue

            best_bidder = None
            highest_bid = 0.0
            for amr in amr_list:
                node = amr.parasite
                winner = node.cbba.state.winning_agents.get(task_id)
                bid = node.cbba.state.winning_bids.get(task_id, 0.0)
                if winner and bid > highest_bid:
                    highest_bid = bid
                    best_bidder = winner

            if best_bidder and highest_bid > 0:
                task.assigned_to = best_bidder
                if task.status == TaskStatus.UNASSIGNED:
                    task.status = TaskStatus.ASSIGNED

                for amr in amr_list:
                    if amr.id != best_bidder and amr.parasite:
                        if task_id in amr.parasite.cbba.state.bundle:
                            amr.parasite.cbba.state.bundle = [
                                t for t in amr.parasite.cbba.state.bundle if t != task_id
                            ]
                        amr.parasite.cbba.state.winning_agents[task_id] = best_bidder
                        amr.parasite.cbba.state.winning_bids[task_id] = highest_bid
                        if amr.parasite.active_task_id == task_id:
                            amr.parasite.active_task_id = None
                            amr.parasite.active_subtask = None
                            amr.path = []
                            amr.state_label = "IDLE"

        # Dynamically sync AMR right-of-way priority from active delivery task
        for amr in self.amrs.values():
            if amr.parasite and amr.parasite.active_task_id:
                active_t = self.tasks.get(amr.parasite.active_task_id)
                amr.priority = active_t.priority if active_t else 1
            else:
                amr.priority = 1

        charging_bays = self.get_charging_nodes()

        # 5. Route autonomous CBBA tasks with dynamic congestion avoidance
        for amr in self.amrs.values():
            if amr.parasite and amr.parasite.is_alive:
                # Recharging logic at any designated charging bay (n12, n13, n14, etc.)
                if amr.current_node in charging_bays and not amr.path:
                    if amr.parasite.battery_soc < 100.0:
                        amr.parasite.recharge(dt)
                        amr.battery = amr.parasite.battery_soc
                        amr.state_label = "CHARGING"
                    elif amr.state_label == "CHARGING":
                        amr.state_label = "IDLE"

                # Autonomous emergency task surrender and return to closest vacant charge bay when battery is low (< 18%)
                if amr.parasite.battery_soc <= 18.0 and amr.current_node not in charging_bays and amr.state_label != "CHARGING":
                    target_charging_bay = self.get_charging_node(from_node=amr.current_node)
                    if amr.parasite.active_task_id:
                        surrender_tid = amr.parasite.active_task_id
                        if surrender_tid in self.tasks:
                            self.tasks[surrender_tid].status = TaskStatus.UNASSIGNED
                            self.tasks[surrender_tid].assigned_to = None
                        amr.parasite.cbba.release_task(surrender_tid, self.tasks)
                        amr.parasite.active_task_id = None
                        amr.parasite.active_subtask = None
                        from backend.nlp.translator import FleetNLPTranslator
                        nlp_trans = FleetNLPTranslator.translate_task_reassign(amr.id, surrender_tid)
                        self.p2p_conversations.append({
                            "time": time.strftime("%H:%M:%S"),
                            "source": amr.id,
                            "target": "ALL_PEERS",
                            "winner": "PEER_FLEET",
                            "yielder": amr.id,
                            "corridor": f"Station {amr.current_node} [LOW_BATTERY]",
                            "machine_protocol": nlp_trans["machine_protocol"],
                            "human_speech": f"[{amr.id} ➔ ALL_PEERS] \"Battery at {amr.parasite.battery_soc}%. Surrendering task {surrender_tid}. Routing to Charging Bay {target_charging_bay}.\"",
                            "message": f"[{amr.id} ➔ ALL_PEERS] \"Battery at {amr.parasite.battery_soc}%. Surrendering task {surrender_tid}. Routing to Charging Bay {target_charging_bay}.\"",
                        })
                        self.p2p_conversations = self.p2p_conversations[-20:]

                    if not amr.path or amr.path[-1] not in charging_bays:
                        try:
                            c_graph = self._build_congested_graph(amr.id)
                            charge_path = astar_path(c_graph, amr.current_node, target_charging_bay, blocked_nodes=failed_nodes)
                            amr.path = charge_path[1:]
                            amr.progress = 0.0
                            amr.state_label = "LOW_BATTERY"
                        except Exception:
                            pass

                # Proactive advance siding evacuation: if a remote AMR is delivering to our station, vacate in advance!
                if not amr.path and not amr.queued_targets and amr.parasite and not amr.parasite.active_task_id:
                    for r_amr in self.remote_amrs.values():
                        r_path = r_amr.get("path", [])
                        if r_path and (r_path[0] == amr.current_node or r_path[-1] == amr.current_node):
                            evac_node = self.traffic_manager.find_evacuation_node(
                                self.graph, amr.current_node, forbidden_nodes={r_amr.get("current_node", "")}
                            )
                            if evac_node:
                                amr.path = [evac_node]
                                amr.progress = 0.0
                                amr.state_label = "TRANSIT"
                                break

                # Route standard CBBA task waypoints using dynamic congestion-weighted graph
                if not amr.path and not amr.queued_targets:
                    # If mesh peers are active, allow a brief 0.6s consensus gossip convergence window
                    # so that if a peer laptop has a closer robot with a higher bid, it wins fairly!
                    candidate_tid = amr.parasite.cbba.state.bundle[0] if amr.parasite.cbba.state.bundle else None
                    candidate_task = self.tasks.get(candidate_tid) if candidate_tid else None
                    in_consensus_sync = bool(
                        candidate_task
                        and self.remote_amrs
                        and (time.time() - candidate_task.created_at) < 0.6
                        and candidate_task.status == TaskStatus.UNASSIGNED
                    )

                    if not in_consensus_sync:
                        prev_active = amr.parasite.active_task_id
                        next_node = amr.parasite.get_next_waypoint(self.tasks, amr.current_node)

                        # Broadcast task completion when dropoff delivery finishes
                        if prev_active and not amr.parasite.active_task_id and prev_active in self.tasks:
                            comp_task = self.tasks[prev_active]
                            if comp_task.status == TaskStatus.COMPLETED:
                                self._record_task_history(comp_task)
                                from backend.nlp.translator import FleetNLPTranslator
                                nlp_trans = FleetNLPTranslator.translate_task_complete(
                                    amr.id, prev_active, comp_task.dropoff_node
                                )
                                self.p2p_conversations.append({
                                    "time": time.strftime("%H:%M:%S"),
                                    "source": amr.id,
                                    "target": "ALL_PEERS",
                                    "winner": amr.id,
                                    "yielder": "N/A",
                                    "corridor": f"Station {comp_task.dropoff_node} [VACANT]",
                                    "machine_protocol": nlp_trans["machine_protocol"],
                                    "human_speech": nlp_trans["human_speech"],
                                    "message": f"[{amr.id} ➔ ALL_PEERS] \"{nlp_trans['human_speech']}\"",
                                })
                                self.p2p_conversations = self.p2p_conversations[-20:]
                                if self.network_manager:
                                    self.network_manager.broadcast_task_status(
                                        prev_active, "COMPLETED", amr.id
                                    )

                        if next_node and next_node != amr.current_node:
                            try:
                                c_graph = self._build_congested_graph(amr.id)
                                path = astar_path(c_graph, amr.current_node, next_node, blocked_nodes=failed_nodes)
                                amr.path = path[1:]
                                amr.progress = 0.0
                                amr.state_label = "TRANSIT"
                            except (nx.NetworkXNoPath, nx.NodeNotFound):
                                pass
            amr.start_next_target_if_idle(self.graph)

        # 6. Move AMRs with smooth sequence motion
        for amr in self.amrs.values():
            if amr.parasite and not amr.parasite.is_alive:
                continue
            self._advance(amr, dt)

        # 7. Physical SAT collision verification
        self._update_collisions()

    def _resolve_traffic_conflict(self, amr: AMR) -> bool:
        """Check if upcoming node/corridor is occupied or contested.

        Executes dynamic in-flight detour calculation, car-following, intersection clearance holding,
        advance evacuation, and P2P right-of-way resolution to prevent collisions and looping.

        Returns:
            True if AMR must wait / yield at current position, False if clear to advance.
        """
        if not amr.path:
            return False

        next_node = amr.path[0]
        safe_gap = max(self.length * 1.5, 1.5)
        safe_node_clearance = max(self.length * 1.2, 1.2)
        failed_nodes = {
            a.current_node
            for a in self.amrs.values()
            if a.state_label == "FAILED" or (a.parasite and not a.parasite.is_alive)
        }

        for other in self.amrs.values():
            if other.id == amr.id:
                continue

            # 1. Other robot is FAILED (hardware obstacle)
            if other.state_label == "FAILED" or (other.parasite and not other.parasite.is_alive):
                if other.current_node == next_node:
                    if amr.path and len(amr.path) > 1:
                        target = amr.path[-1]
                        detour = self.traffic_manager.calculate_detour(
                            self.graph, amr.current_node, target, blocked_nodes={other.current_node}
                        )
                        if detour and len(detour) > 1 and detour[1] != next_node:
                            amr.path = detour[1:]
                            amr.progress = 0.0
                            amr.state_label = "TRANSIT"
                            return False
                    amr.state_label = "YIELDING"
                    return True
                continue

            # 2. Departure from SAME NODE (Car-following or diverging edges)
            if other.current_node == amr.current_node:
                if other.path and other.path[0] == next_node:
                    if other.progress > amr.progress:
                        # 'other' is in front of 'amr'. AMR must maintain safe following gap!
                        if (other.progress - amr.progress) < safe_gap:
                            amr.state_label = "YIELDING"
                            return True
                    continue
                else:
                    # Diverging outgoing edges from same station: grant departure precedence to higher priority or lower ID
                    if amr.progress < 0.2 and getattr(other, "progress", 0.0) < 0.2:
                        p_amr = getattr(amr, "priority", 1) * 1000 + (len(amr.path) if amr.path else 0)
                        p_other = getattr(other, "priority", 1) * 1000 + (len(other.path) if other.path else 0)
                        if p_other > p_amr or (p_other == p_amr and amr.id > other.id):
                            amr.state_label = "YIELDING"
                            return True
                    continue


            # 3. Blocker is at next_node
            if other.current_node == next_node:
                # 3a. Head-on opposing edge (other is at next_node and wants to enter amr.current_node)
                is_opposing = bool(other.path and other.path[0] == amr.current_node)
                if is_opposing:
                    winner_id, yielder_id = self.traffic_manager.resolve_head_on(amr, other, self.graph)
                    if amr.id == yielder_id:
                        # Try dynamic alternate detour avoiding the contested head-on corridor
                        if len(amr.path) > 1:
                            target = amr.path[-1]
                            try:
                                c_graph = self._build_congested_graph(amr.id)
                                detour = astar_path(c_graph, amr.current_node, target, blocked_nodes={next_node}.union(failed_nodes))
                                occupied_first_nodes = {a.current_node for a in self.amrs.values() if a.id != amr.id}
                                if detour and len(detour) > 1 and detour[1] != next_node and detour[1] not in occupied_first_nodes:
                                    amr.path = detour[1:]
                                    amr.progress = 0.0
                                    amr.state_label = "TRANSIT"
                                    return False
                            except (nx.NetworkXNoPath, nx.NodeNotFound):
                                pass

                        amr.state_label = "YIELDING"
                        return True
                    else:
                        # Winner waits until yielder clears or steps aside
                        amr.state_label = "YIELDING"
                        return True

                # 3b. Blocker is vacating along a different edge
                if other.path and other.path[0] != amr.current_node:
                    # Must wait until other has physically cleared next_node by safe distance
                    if other.progress < safe_node_clearance:
                        amr.state_label = "YIELDING"
                        return True
                    # Other cleared the intersection, amr may proceed
                    continue

                # 3c. Blocker is stationary/idle at next_node
                if not other.path or other.state_label in ("IDLE", "YIELDING"):
                    # If other is idle with no active task, trigger advance evacuation
                    if other.parasite and not other.parasite.active_task_id:
                        preferred_forbidden = {amr.current_node}
                        if len(amr.path) > 1:
                            preferred_forbidden.add(amr.path[1])
                        occupied = self.get_occupied_nodes()
                        evac_node = self.traffic_manager.find_evacuation_node(
                            self.graph, other.current_node, forbidden_nodes=preferred_forbidden, occupied_nodes=occupied
                        )
                        if not evac_node:
                            evac_node = self.traffic_manager.find_evacuation_node(
                                self.graph, other.current_node, forbidden_nodes={amr.current_node}, occupied_nodes=occupied
                            )
                        if evac_node and not other.path:
                            other.path = [evac_node]
                            other.progress = 0.0
                            other.state_label = "YIELDING"

                    # If other is busy or cannot evacuate, try alternate detour
                    if (not other.parasite or other.parasite.active_task_id) and len(amr.path) > 1:
                        target = amr.path[-1]
                        try:
                            c_graph = self._build_congested_graph(amr.id)
                            detour = astar_path(c_graph, amr.current_node, target, blocked_nodes={next_node}.union(failed_nodes))
                            occupied_first_nodes = {a.current_node for a in self.amrs.values() if a.id != amr.id}
                            if detour and len(detour) > 1 and detour[1] != next_node and detour[1] not in occupied_first_nodes:
                                amr.path = detour[1:]
                                amr.progress = 0.0
                                amr.state_label = "TRANSIT"
                                return False
                        except (nx.NetworkXNoPath, nx.NodeNotFound):
                            pass

                    amr.state_label = "YIELDING"
                    return True

            # 4. Competing for next_node from DIFFERENT incoming edges (Intersection convergence)
            if other.path and other.path[0] == next_node and other.current_node != amr.current_node:
                # Try dynamic alternate detour around congested intersection
                if len(amr.path) > 1:
                    target = amr.path[-1]
                    try:
                        c_graph = self._build_congested_graph(amr.id)
                        detour = astar_path(c_graph, amr.current_node, target, blocked_nodes={next_node}.union(failed_nodes))
                        if detour and len(detour) > 1 and detour[1] != next_node:
                            amr.path = detour[1:]
                            amr.progress = 0.0
                            amr.state_label = "TRANSIT"
                            return False
                    except (nx.NetworkXNoPath, nx.NodeNotFound):
                        pass

                edge_len_amr = self.graph.edges[amr.current_node, next_node]["weight"]
                rem_amr = max(0.0, edge_len_amr - amr.progress)

                edge_len_other = self.graph.edges[other.current_node, next_node]["weight"]
                rem_other = max(0.0, edge_len_other - other.progress)

                if rem_amr > rem_other + 0.1:
                    if rem_amr < safe_gap:
                        amr.state_label = "YIELDING"
                        return True
                elif rem_other > rem_amr + 0.1:
                    pass
                else:
                    winner_id, yielder_id = self.traffic_manager.resolve_head_on(amr, other, self.graph)
                    if amr.id == yielder_id and rem_amr < safe_gap:
                        amr.state_label = "YIELDING"
                        return True

        # 5. Remote Shadow AMRs from peer laptops across the mesh
        for r_id, r_amr in self.remote_amrs.items():
            r_curr = r_amr.get("current_node")
            r_path = r_amr.get("path", [])

            # 5a. Remote robot is occupying next_node
            if r_curr == next_node:
                if len(amr.path) > 1:
                    target = amr.path[-1]
                    try:
                        c_graph = self._build_congested_graph(amr.id)
                        detour = astar_path(c_graph, amr.current_node, target, blocked_nodes={next_node}.union(failed_nodes))
                        occupied_first = {a.current_node for a in self.amrs.values() if a.id != amr.id}
                        occupied_first.update({r.get("current_node") for r in self.remote_amrs.values() if r.get("current_node")})
                        if detour and len(detour) > 1 and detour[1] != next_node and detour[1] not in occupied_first:
                            amr.path = detour[1:]
                            amr.progress = 0.0
                            amr.state_label = "TRANSIT"
                            return False
                    except (nx.NetworkXNoPath, nx.NodeNotFound):
                        pass

                amr.state_label = "YIELDING"
                return True

            # 5b. Head-on opposing edge with remote robot
            if r_path and r_path[0] == amr.current_node and r_curr == next_node:
                if amr.id > r_id:
                    amr.state_label = "YIELDING"
                    return True

            # 5c. Remote robot converging on next_node from another edge
            if r_path and r_path[0] == next_node and r_curr != amr.current_node:
                if amr.id > r_id:
                    amr.state_label = "YIELDING"
                    return True

        if amr.state_label == "YIELDING":
            amr.state_label = "TRANSIT"
            amr.yield_start_time = 0.0
        return False

    def _advance(self, amr: AMR, dt: float) -> None:
        if self._resolve_traffic_conflict(amr):
            if amr.state_label == "YIELDING" and getattr(amr, "yield_start_time", 0.0) == 0.0:
                amr.yield_start_time = time.time()
            self._update_position(amr)
            return

        if amr.state_label in ("IDLE", "YIELDING", "BIDDING") and amr.path:
            amr.state_label = "TRANSIT"
            amr.yield_start_time = 0.0

        remaining = self.speed * dt
        distance_moved = 0.0

        while remaining > 0 and amr.path:
            target_node = amr.path[0]
            if self._resolve_traffic_conflict(amr):
                break
            edge_length = self.graph.edges[amr.current_node, target_node]["weight"]
            remaining_on_edge = edge_length - amr.progress
            step_move = min(remaining, remaining_on_edge)

            # Spatial lookahead check: Verify candidate next position does not collide with any active AMR
            from_n = self.graph.nodes[amr.current_node]
            to_n = self.graph.nodes[target_node]
            cand_prog = amr.progress + step_move
            fraction = cand_prog / edge_length if edge_length else 0.0
            cand_x = from_n["x"] + (to_n["x"] - from_n["x"]) * fraction
            cand_y = from_n["y"] + (to_n["y"] - from_n["y"]) * fraction
            cand_heading = math.atan2(to_n["y"] - from_n["y"], to_n["x"] - from_n["x"])
            cand_rect = (cand_x, cand_y, cand_heading, self.width, self.length)

            collision_imminent = False
            for other in self.amrs.values():
                if other.id != amr.id and other.state_label != "FAILED":
                    # If other is stationary at our origin node and we are moving away along our path, allow departure
                    if other.current_node == amr.current_node and getattr(other, "progress", 0.0) < 0.2 and (not other.path or other.path[0] != target_node):
                        continue
                    other_rect = (other.x, other.y, other.heading, self.width, self.length)
                    if rectangles_overlap(cand_rect, other_rect):
                        collision_imminent = True
                        break

            if not collision_imminent:
                for r_amr in self.remote_amrs.values():
                    pos = r_amr.get("position", {})
                    rx = pos.get("x", 0.0)
                    ry = pos.get("y", 0.0)
                    rh = r_amr.get("heading", 0.0)
                    r_rect = (rx, ry, rh, self.width, self.length)
                    if rectangles_overlap(cand_rect, r_rect):
                        collision_imminent = True
                        break

            if collision_imminent:
                amr.state_label = "YIELDING"
                break

            if remaining < remaining_on_edge:
                amr.progress += remaining
                distance_moved += remaining
                remaining = 0.0
            else:
                remaining -= remaining_on_edge
                distance_moved += remaining_on_edge
                amr.current_node = amr.path.pop(0)
                amr.progress = 0.0

        if amr.parasite:
            has_payload = bool(
                amr.parasite.active_task_id and amr.parasite.active_subtask == "DROPOFF"
            )
            amr.parasite.drain_battery(
                distance_traveled=distance_moved,
                dt=dt,
                speed=self.speed,
                has_payload=has_payload,
            )
            amr.battery = amr.parasite.battery_soc

        if not amr.path and amr.state_label not in ("FAILED", "CHARGING"):
            amr.state_label = "IDLE"

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
        amrs = [a for a in self.amrs.values() if a.state_label != "FAILED"]
        for amr in self.amrs.values():
            amr.colliding = False
        for i in range(len(amrs)):
            for j in range(i + 1, len(amrs)):
                a, b = amrs[i], amrs[j]
                rect_a = (a.x, a.y, a.heading, self.width, self.length)
                rect_b = (b.x, b.y, b.heading, self.width, self.length)
                if rectangles_overlap(rect_a, rect_b):
                    a.colliding = True
                    b.colliding = True

    def rename_amr(self, amr_id: str, new_name: str) -> bool:
        """Update an AMR's display name."""
        with self._amrs_lock:
            if amr := self.amrs.get(amr_id):
                amr.custom_name = new_name
                return True
            return False

    def snapshot(self) -> list[dict]:
        local_snapshots = [
            {
                "id": amr.id,
                "name": amr.custom_name or amr.id,
                "current_node": amr.current_node,
                "position": {"x": amr.x, "y": amr.y},
                "heading": amr.heading,
                "path": list(amr.path),
                "queued_targets": list(amr.queued_targets),
                "colliding": amr.colliding,
                "state_label": amr.state_label,
                "battery_soc": amr.parasite.battery_soc if amr.parasite else 100.0,
                "active_task": amr.parasite.active_task_id if amr.parasite else None,
                "bundle": amr.parasite.cbba.state.bundle if amr.parasite else [],
                "is_remote": False,
            }
            for amr in self.amrs.values()
        ]
        remote_snapshots = [
            {
                "id": r_amr.get("id"),
                "name": r_amr.get("name") or r_amr.get("id"),
                "current_node": r_amr.get("current_node"),
                "position": r_amr.get("position", {"x": 0.0, "y": 0.0}),
                "heading": r_amr.get("heading", 0.0),
                "path": r_amr.get("path", []),
                "queued_targets": [],
                "colliding": False,
                "state_label": r_amr.get("state_label", "IDLE"),
                "battery_soc": r_amr.get("battery_soc", 100.0),
                "active_task": r_amr.get("active_task"),
                "bundle": [],
                "is_remote": True,
            }
            for r_amr in self.remote_amrs.values()
        ]
        return local_snapshots + remote_snapshots

