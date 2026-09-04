import json
import os
import socket
import sys
import uuid
from typing import Protocol

BEACON_PORT_DEFAULT = int(os.environ.get("BEACON_PORT", 9999))


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


class UDPNetworkManager:
    """Decentralized Multi-Laptop UDP Mesh Network Manager.

    Handles real-time P2P message transmission, incoming packet dispatch,
    and Round-Robin Time-Division Gossip sequencing to eliminate network lag.
    """

    def __init__(
        self,
        port: int = BEACON_PORT_DEFAULT,
        broadcast_address: str = "255.255.255.255",
        fleet_prefix: str = "",
        peer_ips: list[str] | None = None,
    ):
        self.port = port
        self.fleet_prefix = fleet_prefix
        self.host_id = uuid.uuid4().hex[:8]

        # Detect all local subnet broadcast addresses (e.g. 10.1.0.255, 192.168.1.255)
        self.broadcast_targets = self._detect_broadcast_targets(broadcast_address)
        if peer_ips:
            for ip in peer_ips:
                if ip and ip not in self.broadcast_targets:
                    self.broadcast_targets.append(ip)

        # Send socket
        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._send_sock.settimeout(0.5)

        # Receive socket
        self._recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except Exception:
                pass
        try:
            self._recv_sock.bind(("", port))
        except OSError:
            pass  # If port is already bound on some platforms, continue gracefully

        self._running = True

    def _detect_broadcast_targets(self, default_target: str) -> list[str]:
        targets = [default_target, "<broadcast>", "255.255.255.255"]
        try:
            hostname = socket.gethostname()
            for ip in socket.gethostbyname_ex(hostname)[2]:
                parts = ip.split(".")
                if len(parts) == 4 and not ip.startswith("127."):
                    subnet_broadcast = f"{parts[0]}.{parts[1]}.{parts[2]}.255"
                    if subnet_broadcast not in targets:
                        targets.append(subnet_broadcast)
        except Exception:
            pass
        return list(dict.fromkeys(targets))

    def broadcast_packet(self, packet: dict) -> None:
        """Broadcast a JSON datagram to the UDP mesh across all network interfaces."""
        packet["sender_host"] = self.host_id
        packet["fleet_prefix"] = self.fleet_prefix
        try:
            payload = json.dumps(packet).encode("utf-8")
            for target in self.broadcast_targets:
                try:
                    self._send_sock.sendto(payload, (target, self.port))
                except OSError:
                    pass
        except Exception:
            pass

    def broadcast_task_announce(self, task_dict: dict) -> None:
        """Broadcast a new warehouse task announcement across all laptops."""
        self.broadcast_packet({"type": "TASK_ANNOUNCE", "task": task_dict})

    def broadcast_cbba_gossip(self, agent_id: str, consensus_dict: dict) -> None:
        """Broadcast CBBA consensus bidding state for an AMR."""
        self.broadcast_packet(
            {"type": "CBBA_GOSSIP", "agent_id": agent_id, "consensus": consensus_dict}
        )

    def broadcast_amr_beacon(self, amr_snapshot: dict) -> None:
        """Broadcast real-time AMR coordinates and status beacon."""
        self.broadcast_packet({"type": "AMR_BEACON", "amr": amr_snapshot})

    def broadcast_task_status(self, task_id: str, status: str, assigned_to: str) -> None:
        """Broadcast task lifecycle transition (e.g. IN_PROGRESS, COMPLETED)."""
        self.broadcast_packet(
            {
                "type": "TASK_STATUS",
                "task_id": task_id,
                "status": status,
                "assigned_to": assigned_to,
            }
        )

    async def listen_loop(self, on_packet_received) -> None:
        """Asynchronously listen for incoming UDP packets and dispatch them."""
        import asyncio

        loop = asyncio.get_running_loop()
        while self._running:
            try:
                # Use run_in_executor to avoid blocking the event loop on recvfrom
                payload = await loop.run_in_executor(
                    None, self._blocking_recv
                )
                if payload:
                    packet = json.loads(payload.decode("utf-8"))
                    # Filter out packets broadcast by our own host
                    if packet.get("sender_host") != self.host_id:
                        on_packet_received(packet)
            except Exception:
                await asyncio.sleep(0.01)

    def _blocking_recv(self) -> bytes | None:
        self._recv_sock.settimeout(0.5)
        try:
            payload, _ = self._recv_sock.recvfrom(4096)
            return payload
        except (socket.timeout, OSError):
            return None

    async def round_robin_gossip_loop(self, simulation, tick_interval: float = 0.05) -> None:
        """Execute Round-Robin Time-Division Gossip broadcasting.

        Cycles through local AMRs one-by-one so only 1 AMR broadcasts per time slot,
        preventing packet storms, socket congestion, and lag.
        """
        import asyncio

        amr_keys = list(simulation.amrs.keys())
        idx = 0
        while self._running:
            if amr_keys:
                amr_id = amr_keys[idx % len(amr_keys)]
                idx += 1
                amr = simulation.amrs.get(amr_id)
                if amr:
                    # 1. Broadcast AMR Beacon
                    beacon_data = {
                        "id": amr.id,
                        "current_node": amr.current_node,
                        "position": {"x": amr.x, "y": amr.y},
                        "heading": amr.heading,
                        "path": list(amr.path),
                        "state_label": amr.state_label,
                        "battery_soc": amr.parasite.battery_soc if amr.parasite else 100.0,
                        "active_task": amr.parasite.active_task_id if amr.parasite else None,
                    }
                    self.broadcast_amr_beacon(beacon_data)

                    # 2. Broadcast CBBA Gossip state if companion is active
                    if amr.parasite and amr.parasite.is_alive:
                        self.broadcast_cbba_gossip(amr.id, amr.parasite.cbba.state.to_dict())

            await asyncio.sleep(tick_interval)

    def close(self) -> None:
        self._running = False
        try:
            self._send_sock.close()
            self._recv_sock.close()
        except Exception:
            pass

