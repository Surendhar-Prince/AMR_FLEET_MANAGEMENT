import json
import socket
import sys
from typing import Protocol

BEACON_PORT_DEFAULT = 9999


def encode_beacon_message(amr_id: str, x: float, y: float, goal: str | None) -> bytes:
    return json.dumps({"amr_id": amr_id, "x": x, "y": y, "goal": goal}).encode("utf-8")


def decode_beacon_message(payload: bytes) -> dict:
    return json.loads(payload.decode("utf-8"))


def goal_from_path(path: list[str]) -> str | None:
    """Return an AMR's final destination node, or None if it has no active route."""
    return path[-1] if path else None


class _Publisher(Protocol):
    def publish(self, amr_id: str, x: float, y: float, goal: str | None) -> None: ...


def publish_snapshot(
    publisher: _Publisher, snapshot: list[dict], warned: set[str] | None = None
) -> None:
    """Publish one beacon per AMR in the snapshot.

    A failed send (e.g. a transient network error) is logged and skipped so
    it does not stop beacons for the remaining AMRs or kill the caller's loop.
    Each amr_id is warned about at most once (via `warned`) so a persistent
    failure does not flood stderr forever on every publish interval.
    """
    if warned is None:
        warned = set()
    for state in snapshot:
        try:
            publisher.publish(
                state["id"],
                state["position"]["x"],
                state["position"]["y"],
                goal_from_path(state["path"]),
            )
        except OSError as exc:
            if state["id"] not in warned:
                warned.add(state["id"])
                print(f"beacon: failed to publish for {state['id']}: {exc}", file=sys.stderr)


class BeaconPublisher:
    """Broadcasts one AMR's position as a UDP datagram per publish() call."""

    def __init__(
        self, port: int = BEACON_PORT_DEFAULT, broadcast_address: str = "255.255.255.255"
    ):
        self.port = port
        self.broadcast_address = broadcast_address
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Bounds sendto() so an OS-level stall (e.g. macOS Local Network
        # privacy checks on broadcast traffic) becomes a fast OSError instead
        # of blocking the calling thread — and therefore the whole asyncio
        # event loop, including signal handling — forever.
        self._sock.settimeout(1.0)

    def publish(self, amr_id: str, x: float, y: float, goal: str | None) -> None:
        payload = encode_beacon_message(amr_id, x, y, goal)
        self._sock.sendto(payload, (self.broadcast_address, self.port))

    def close(self) -> None:
        self._sock.close()


class BeaconListener:
    """Receives AMR position beacons broadcast on a UDP port."""

    def __init__(self, port: int = BEACON_PORT_DEFAULT):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("", port))
        self.port = self._sock.getsockname()[1]

    def receive(self, timeout: float | None = None) -> dict:
        self._sock.settimeout(timeout)
        payload, _addr = self._sock.recvfrom(4096)
        return decode_beacon_message(payload)

    def close(self) -> None:
        self._sock.close()
