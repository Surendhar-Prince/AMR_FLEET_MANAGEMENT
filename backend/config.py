import json
import os
from dataclasses import dataclass

from backend.beacon import BEACON_PORT_DEFAULT


@dataclass
class Config:
    map: str
    port: int
    tick_hz: int
    amr_speed: float
    amr_width: float
    amr_length: float
    amrs: list[dict]
    host: str = "0.0.0.0"
    beacon_port: int = BEACON_PORT_DEFAULT
    beacon_interval_s: float = 1.0
    fleet_prefix: str = ""
    p2p_mesh_enabled: bool = True
    peer_ips: list[str] | None = None


def load_config(path: str) -> Config:
    """Load and validate the simulation config JSON with environment variable overrides."""
    with open(path) as f:
        data = json.load(f)

    fleet_prefix = data.get("fleet_prefix", "")
    amrs = []
    for amr_cfg in data.get("amrs", []):
        cfg_copy = dict(amr_cfg)
        if fleet_prefix and not cfg_copy["id"].startswith(f"{fleet_prefix}-"):
            cfg_copy["id"] = f"{fleet_prefix}-{cfg_copy['id']}"
        amrs.append(cfg_copy)

    # Environment variable overrides
    env_port = os.environ.get("PORT")
    port = int(env_port) if env_port and env_port.isdigit() else data.get("port", 8000)

    env_beacon_port = os.environ.get("BEACON_PORT")
    beacon_port = int(env_beacon_port) if env_beacon_port and env_beacon_port.isdigit() else data.get("beacon_port", BEACON_PORT_DEFAULT)

    env_host = os.environ.get("HOST", "0.0.0.0")

    peer_ips = data.get("peer_ips", [])
    env_peers = os.environ.get("PEER_IPS", "")
    if env_peers:
        peer_ips = [ip.strip() for ip in env_peers.split(",") if ip.strip()]

    return Config(
        map=data["map"],
        port=port,
        host=env_host,
        tick_hz=data["tick_hz"],
        amr_speed=data["amr_speed"],
        amr_width=data["amr_width"],
        amr_length=data["amr_length"],
        amrs=amrs,
        beacon_port=beacon_port,
        beacon_interval_s=data.get("beacon_interval_s", 1.0),
        fleet_prefix=fleet_prefix,
        p2p_mesh_enabled=data.get("p2p_mesh_enabled", True),
        peer_ips=peer_ips,
    )

