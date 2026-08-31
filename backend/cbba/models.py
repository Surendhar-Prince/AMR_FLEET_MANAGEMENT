import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    UNASSIGNED = "UNASSIGNED"
    BIDDING = "BIDDING"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class Task:
    id: str
    pickup_node: str
    dropoff_node: str
    priority: int = 1  # higher = more urgent
    status: TaskStatus = TaskStatus.UNASSIGNED
    assigned_to: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pickup_node": self.pickup_node,
            "dropoff_node": self.dropoff_node,
            "priority": self.priority,
            "status": self.status.value,
            "assigned_to": self.assigned_to,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


@dataclass
class Bid:
    agent_id: str
    task_id: str
    bid_value: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class ConsensusState:
    agent_id: str
    bundle: list[str] = field(default_factory=list)  # Ordered task IDs in bundle b_i
    path: list[str] = field(default_factory=list)    # Ordered node waypoints p_i
    winning_bids: dict[str, float] = field(default_factory=dict)     # y_i(j): max bid on task j
    winning_agents: dict[str, str] = field(default_factory=dict)     # z_i(j): winner of task j
    timestamp_matrix: dict[str, float] = field(default_factory=dict) # s_i(k): timestamp of info from agent k

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "bundle": list(self.bundle),
            "path": list(self.path),
            "winning_bids": dict(self.winning_bids),
            "winning_agents": dict(self.winning_agents),
            "timestamp_matrix": dict(self.timestamp_matrix),
        }
