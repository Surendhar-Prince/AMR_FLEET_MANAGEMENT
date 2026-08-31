"""Bilingual Natural Language & Machine Protocol Translator for Autonomous Fleets.

Converts low-level UDP packets and CBBA consensus states into natural,
human-readable conversational speech while preserving technical telemetry.
"""

from typing import Any, Dict


class FleetNLPTranslator:
    """Translates inter-robot decentralized telemetry into natural human dialogue."""

    @staticmethod
    def translate_p2p_yield(source: str, target: str, winner: str, yielder: str, corridor: str, detour_node: str = "") -> Dict[str, str]:
        if yielder == source:
            human_msg = f"Hey {target}! I see you approaching corridor ({corridor}). You have right-of-way, so I am yielding and taking the detour path{' via Station ' + detour_node if detour_node else ''}."
        else:
            human_msg = f"Attention {target}: I am claiming right-of-way on corridor ({corridor}) for my active delivery. Thank you for yielding."

        return {
            "machine_protocol": f"UDP::TRAFFIC_ACK(win={winner}, yld={yielder}, corr={corridor})",
            "human_speech": human_msg,
        }

    @staticmethod
    def translate_cbba_bid(agent_id: str, task_id: str, pickup: str, dropoff: str, bid_val: float, battery_soc: float) -> Dict[str, str]:
        human_msg = f"Fleet broadcast from {agent_id}: I'm nearby Station {pickup} with {battery_soc}% battery. Placing a bid of {bid_val:.1f} to deliver task [{task_id}] to Station {dropoff}."
        return {
            "machine_protocol": f"UDP::CBBA_BID(agent={agent_id}, task={task_id}, score={bid_val:.1f})",
            "human_speech": human_msg,
        }

    @staticmethod
    def translate_consensus_win(agent_id: str, task_id: str, pickup: str, dropoff: str) -> Dict[str, str]:
        human_msg = f"Consensus reached! {agent_id} won the auction for [{task_id}] ({pickup} ➔ {dropoff}). Now en route to pickup."
        return {
            "machine_protocol": f"UDP::CONSENSUS_LOCK(winner={agent_id}, task={task_id})",
            "human_speech": human_msg,
        }

    @staticmethod
    def translate_task_complete(agent_id: str, task_id: str, dropoff: str) -> Dict[str, str]:
        human_msg = f"Delivery confirmed! {agent_id} successfully dropped off payload for [{task_id}] at Station {dropoff}. Returning to idle pool."
        return {
            "machine_protocol": f"UDP::TASK_DONE(agent={agent_id}, task={task_id}, node={dropoff})",
            "human_speech": human_msg,
        }

    @staticmethod
    def translate_fault_failover(failed_agent: str, rescuer_agent: str, task_id: str) -> Dict[str, str]:
        human_msg = f"Alert: {failed_agent} went offline! {rescuer_agent} detected heartbeat timeout and is taking over [{task_id}] automatically."
        return {
            "machine_protocol": f"UDP::FAILOVER_TAKEOVER(lost={failed_agent}, rescue={rescuer_agent}, task={task_id})",
            "human_speech": human_msg,
        }

    @staticmethod
    def translate_node_offline_quarantine(failed_agent: str, last_node: str) -> Dict[str, str]:
        human_msg = f"📡 Mesh Gossip: {failed_agent} went offline at Station {last_node}! All edge nodes have quarantined Station {last_node} and rerouted traffic."
        return {
            "machine_protocol": f"UDP_BROADCAST::NODE_QUARANTINE(lost_agent={failed_agent}, last_known_pos={last_node}, port=9999)",
            "human_speech": human_msg,
        }
