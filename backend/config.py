import json
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
    beacon_port: int = BEACON_PORT_DEFAULT
    beacon_interval_s: float = 1.0
    fleet_prefix: str = ""
    p2p_mesh_enabled: bool = True
    peer_ips: list[str] | None = None


def load_config(path: str) -> Config:
    """Load and validate the simulation config JSON.

    Args:
        path: Path to a sim_config.json file.

    Returns:
        A populated Config.

    Raises:
        ValueError: If the amrs list is empty.
    """
    with open(path) as f:
        data = json.load(f)

    if not data["amrs"]:
        raise ValueError("config must list at least one AMR")

    fleet_prefix = data.get("fleet_prefix", "")
    amrs = []
    for amr_cfg in data["amrs"]:
        cfg_copy = dict(amr_cfg)
        if fleet_prefix and not cfg_copy["id"].startswith(f"{fleet_prefix}-"):
            cfg_copy["id"] = f"{fleet_prefix}-{cfg_copy['id']}"
        amrs.append(cfg_copy)

    return Config(
        map=data["map"],
        port=data["port"],
        tick_hz=data["tick_hz"],
        amr_speed=data["amr_speed"],
        amr_width=data["amr_width"],
        amr_length=data["amr_length"],
        amrs=amrs,
        beacon_port=data.get("beacon_port", BEACON_PORT_DEFAULT),
        beacon_interval_s=data.get("beacon_interval_s", 1.0),
        fleet_prefix=fleet_prefix,
        p2p_mesh_enabled=data.get("p2p_mesh_enabled", True),
        peer_ips=data.get("peer_ips", []),
    )
