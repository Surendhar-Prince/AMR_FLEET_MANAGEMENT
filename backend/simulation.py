import math
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
        self.remote_amrs: dict[str, dict] = {}
        self.network_packets_count = 0
        self.network_manager = None
        self.last_p2p_events: list[dict] = []
        self.p2p_conversations: list[dict] = []

        self.amrs: dict[str, AMR] = {
            cfg["id"]: AMR.at_node(
                amr_id=cfg["id"],
                node_id=cfg["start_node"],
                graph=graph,
                initial_battery=cfg.get("battery", 100.0),
            )
            for cfg in amr_configs
        }

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

        if broadcast and self.network_manager:
            self.network_manager.broadcast_task_announce(task.to_dict())

        return task

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

        # 4. Task status update broadcast (e.g. peer completed a delivery)
        elif p_type == "TASK_STATUS":
            tid = packet.get("task_id")
            status = packet.get("status")
            assigned_to = packet.get("assigned_to")
            if tid in self.tasks and status:
                self.tasks[tid].status = TaskStatus(status)
                if assigned_to:
                    self.tasks[tid].assigned_to = assigned_to

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
        """Main simulation tick."""
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

        # 5. Route autonomous CBBA tasks with dynamic congestion avoidance
        for amr in self.amrs.values():
            if amr.parasite and amr.parasite.is_alive:
                # Recharging logic at charging dock (Station n14)
                if amr.current_node == "n14" and not amr.path:
                    if amr.parasite.battery_soc < 100.0:
                        amr.parasite.recharge(dt)
                        amr.state_label = "CHARGING"
                    elif amr.state_label == "CHARGING":
                        amr.state_label = "IDLE"

                # Autonomous return to charge bay when battery is low (< 25%) and idle
                if (
                    amr.parasite.battery_soc < 25.0
                    and not amr.path
                    and not amr.queued_targets
                    and not amr.parasite.active_task_id
                    and amr.current_node != "n14"
                ):
                    try:
                        charge_path = astar_path(self.graph, amr.current_node, "n14", blocked_nodes=failed_nodes)
                        amr.path = charge_path[1:]
                        amr.progress = 0.0
                        amr.state_label = "LOW_BATTERY"
                    except Exception:
                        pass

                # Route standard CBBA task waypoints using dynamic congestion-weighted graph
                if not amr.path and not amr.queued_targets:
                    next_node = amr.parasite.get_next_waypoint(self.tasks, amr.current_node)
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

            # 2. Car-Following on the SAME EDGE (Same current_node and same next_node)
            if other.current_node == amr.current_node and other.path and other.path[0] == next_node:
                if other.progress > amr.progress:
                    # 'other' is in front of 'amr'. AMR must maintain safe following gap!
                    if (other.progress - amr.progress) < safe_gap:
                        amr.state_label = "YIELDING"
                        return True
                # If amr is in front of other, amr has right of way to advance
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
                        evac_node = self.traffic_manager.find_evacuation_node(
                            self.graph, other.current_node, forbidden_nodes=preferred_forbidden
                        )
                        if not evac_node:
                            evac_node = self.traffic_manager.find_evacuation_node(
                                self.graph, other.current_node, forbidden_nodes={amr.current_node}
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

        if amr.state_label == "YIELDING" and amr.path:
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
            amr.parasite.drain_battery(distance_moved)

        if not amr.path and amr.state_label != "FAILED":
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

    def snapshot(self) -> list[dict]:
        local_snapshots = [
            {
                "id": amr.id,
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
