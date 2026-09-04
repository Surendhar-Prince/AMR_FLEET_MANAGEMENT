import math
import time
from typing import Optional
import networkx as nx

from backend.cbba.models import ConsensusState, Task, TaskStatus
from backend.map import astar_path, path_length


class CBBAEngine:
    """Decentralized Consensus-Based Bundle Algorithm (CBBA) execution engine for a single AMR."""

    def __init__(
        self,
        agent_id: str,
        graph: nx.DiGraph,
        max_bundle_size: int = 3,
    ):
        self.agent_id = agent_id
        self.graph = graph
        self.max_bundle_size = max_bundle_size
        self.state = ConsensusState(agent_id=agent_id)

    def calculate_bid(
        self,
        task: Task,
        current_node: str,
        battery_soc: float = 100.0,
        occupied_nodes: Optional[set[str]] = None,
        failed_nodes: Optional[set[str]] = None,
    ) -> float:
        """Calculate marginal score bid for a task based on A* distance with congestion awareness, priority, and battery."""
        # 1. HARD SAFETY CHECK: If dropoff destination dock is physically blocked by a failed AMR, NEVER BID!
        if failed_nodes and task.dropoff_node in failed_nodes:
            return 0.0

        try:
            # Build dynamic congestion graph with extra weight on occupied stations
            congested_graph = self.graph.copy()
            if occupied_nodes:
                for node in occupied_nodes:
                    if node in congested_graph:
                        for u, v, d in congested_graph.in_edges(node, data=True):
                            d["weight"] = d.get("weight", 1.0) + 15.0  # Congestion penalty for displacement
                        for u, v, d in congested_graph.out_edges(node, data=True):
                            d["weight"] = d.get("weight", 1.0) + 15.0

            # 1. A* Cost from current position to pickup node
            path_to_pickup = astar_path(congested_graph, current_node, task.pickup_node, blocked_nodes=failed_nodes)
            cost_to_pickup = path_length(congested_graph, path_to_pickup)

            # 2. A* Cost from pickup node to dropoff node
            path_to_dropoff = astar_path(congested_graph, task.pickup_node, task.dropoff_node, blocked_nodes=failed_nodes)
            cost_to_dropoff = path_length(congested_graph, path_to_dropoff)

            # 3. Distance from dropoff node to the nearest charging bay
            charging_nodes = [
                n for n, d in self.graph.nodes(data=True)
                if d.get("type") == "charging" or "charge" in str(n).lower()
            ]
            if not charging_nodes:
                charging_nodes = ["n14"] if "n14" in self.graph.nodes else list(self.graph.nodes)[:1]

            cost_to_charger = float("inf")
            for bay in charging_nodes:
                try:
                    p_charge = astar_path(congested_graph, task.dropoff_node, bay, blocked_nodes=failed_nodes)
                    d_charge = path_length(congested_graph, p_charge)
                    if d_charge < cost_to_charger:
                        cost_to_charger = d_charge
                except Exception:
                    pass
            if cost_to_charger == float("inf"):
                cost_to_charger = 0.0

            # 4. Total round-trip predictive energy calculation
            # Motion drain rate: 0.38 per unit distance. Payload multiplier: 1.6
            e_pickup = cost_to_pickup * 0.38 * 1.0
            e_dropoff = cost_to_dropoff * 0.38 * 1.6
            e_charger = cost_to_charger * 0.38 * 1.0
            e_total_required = e_pickup + e_dropoff + e_charger + 6.0  # 6.0% reserve buffer

            # HARD FEASIBILITY CHECK: if battery cannot sustain round-trip + return to charger, NEVER BID!
            if battery_soc < e_total_required:
                return 0.0

            # 5. Dynamic Backtrack & Turnaround Penalty (Penalize driving back-and-forth over identical corridor)
            backtrack_penalty = 0.0
            if len(path_to_pickup) > 1 and len(path_to_dropoff) > 1:
                pickup_edges = set(zip(path_to_pickup[:-1], path_to_pickup[1:]))
                dropoff_reverse_edges = set(zip(path_to_dropoff[1:], path_to_dropoff[:-1]))
                overlap_count = len(pickup_edges.intersection(dropoff_reverse_edges))
                backtrack_penalty = overlap_count * 8.0  # Turnaround & double-transit traffic latency

            # 6. Queue Load Balancing Penalty: busy robots with queued tasks defer to idle robots
            queue_penalty = len(self.state.bundle) * 15.0

            total_true_cost = cost_to_pickup + cost_to_dropoff + backtrack_penalty + queue_penalty
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return 0.0

        # Reward formula: higher priority, lower true delivery cost, and idle availability bonus
        idle_bonus = 1.4 if len(self.state.bundle) == 0 else 1.0
        battery_factor = max(0.1, battery_soc / 100.0)
        base_bid = ((100.0 * task.priority) / (1.0 + total_true_cost)) * idle_bonus
        return float(round(base_bid * battery_factor, 3))

    def phase1_build_bundle(
        self,
        tasks: dict[str, Task],
        current_node: str,
        battery_soc: float = 100.0,
        occupied_nodes: Optional[set[str]] = None,
        failed_nodes: Optional[set[str]] = None,
    ) -> bool:
        """Phase 1: Greedily add tasks to bundle while capacity allows and bid outstrips current winning bid.

        Returns:
            True if bundle or bids were modified.
        """
        changed = False

        while len(self.state.bundle) < self.max_bundle_size:
            best_task_id: Optional[str] = None
            best_bid = 0.0
            best_marginal_gain = 0.0

            # Reference position for sequential chaining
            ref_node = current_node
            if self.state.bundle:
                last_task_id = self.state.bundle[-1]
                if last_task_id in tasks:
                    ref_node = tasks[last_task_id].dropoff_node

            for task_id, task in tasks.items():
                if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.IN_PROGRESS):
                    continue
                if task_id in self.state.bundle:
                    continue

                bid_val = self.calculate_bid(
                    task, ref_node, battery_soc, occupied_nodes=occupied_nodes, failed_nodes=failed_nodes
                )
                current_winner_bid = self.state.winning_bids.get(task_id, 0.0)
                winner_agent = self.state.winning_agents.get(task_id, "")
                if winner_agent == self.agent_id:
                    marginal_gain = bid_val
                else:
                    marginal_gain = bid_val - current_winner_bid

                if marginal_gain > best_marginal_gain and bid_val > 0.0:
                    best_marginal_gain = marginal_gain
                    best_bid = bid_val
                    best_task_id = task_id

            if best_task_id and best_bid > 0:
                self.state.bundle.append(best_task_id)
                self.state.winning_bids[best_task_id] = best_bid
                self.state.winning_agents[best_task_id] = self.agent_id
                self.state.timestamp = time.time()
                changed = True
            else:
                break

        return changed

    def phase2_consensus(
        self,
        peer_state: ConsensusState,
        tasks: dict[str, Task],
    ) -> bool:
        """Phase 2: Decentralized Consensus conflict resolution against a peer state.

        Applies CBBA Consensus Decision Matrix (Table 1 from CBBA paper).

        Returns:
            True if our bundle or winning assignments were updated.
        """
        changed = False
        all_task_ids = set(self.state.winning_bids.keys()).union(peer_state.winning_bids.keys())
        first_lost_bundle_idx: Optional[int] = None

        for task_id in all_task_ids:
            my_winner = self.state.winning_agents.get(task_id, "")
            peer_winner = peer_state.winning_agents.get(task_id, "")
            my_bid = self.state.winning_bids.get(task_id, 0.0)
            peer_bid = peer_state.winning_bids.get(task_id, 0.0)

            # 1. Peer outbids us
            if peer_bid > my_bid:
                self.state.winning_bids[task_id] = peer_bid
                self.state.winning_agents[task_id] = peer_winner
                changed = True

                # If this task was in our bundle, record bundle truncation point
                if task_id in self.state.bundle:
                    idx = self.state.bundle.index(task_id)
                    if first_lost_bundle_idx is None or idx < first_lost_bundle_idx:
                        first_lost_bundle_idx = idx

            # 2. Peer confirms our win
            elif my_bid > peer_bid and my_winner == self.agent_id:
                pass  # We retain ownership

            # 3. Tie-breaking on equal bids (Deterministic lowest agent_id wins)
            elif math.isclose(peer_bid, my_bid, rel_tol=1e-5) and peer_bid > 0.0:
                if peer_winner < my_winner:
                    self.state.winning_agents[task_id] = peer_winner
                    changed = True
                    if task_id in self.state.bundle:
                        idx = self.state.bundle.index(task_id)
                        if first_lost_bundle_idx is None or idx < first_lost_bundle_idx:
                            first_lost_bundle_idx = idx

        # Cascade bundle trimming: Drop all tasks from first_lost_bundle_idx onwards
        if first_lost_bundle_idx is not None:
            orphaned_tasks = self.state.bundle[first_lost_bundle_idx:]
            self.state.bundle = self.state.bundle[:first_lost_bundle_idx]
            for t_id in orphaned_tasks:
                if self.state.winning_agents.get(t_id) == self.agent_id:
                    self.state.winning_agents[t_id] = ""
                    self.state.winning_bids[t_id] = 0.0
            changed = True

        if changed:
            self.state.timestamp = time.time()

        return changed

    def release_task(self, task_id: str, tasks: dict[str, Task]) -> None:
        """Release a task from bundle and reset local winning assignment."""
        if task_id in self.state.bundle:
            self.state.bundle.remove(task_id)
        if self.state.winning_agents.get(task_id) == self.agent_id:
            self.state.winning_agents[task_id] = ""
            self.state.winning_bids[task_id] = 0.0
        self.state.timestamp = time.time()
