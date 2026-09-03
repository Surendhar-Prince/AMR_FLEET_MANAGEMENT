import json

import pytest

from backend.config import load_config


def write_config(tmp_path, data):
    path = tmp_path / "sim_config.json"
    path.write_text(json.dumps(data))
    return str(path)


def test_load_config_returns_expected_fields(tmp_path):
    path = write_config(
        tmp_path,
        {
            "map": "maps/sample_map.json",
            "port": 8000,
            "tick_hz": 20,
            "amr_speed": 1.0,
            "amr_width": 0.8,
            "amr_length": 1.2,
            "amrs": [{"id": "amr-1", "start_node": "n1"}],
        },
    )

    config = load_config(path)

    assert config.map == "maps/sample_map.json"
    assert config.port == 8000
    assert config.tick_hz == 20
    assert config.amr_speed == 1.0
    assert config.amr_width == 0.8
    assert config.amr_length == 1.2
    assert config.amrs == [{"id": "amr-1", "start_node": "n1"}]
    assert config.beacon_port == 9999


def test_load_config_defaults_beacon_port_when_absent(tmp_path):
    path = write_config(
        tmp_path,
        {
            "map": "maps/sample_map.json",
            "port": 8000,
            "tick_hz": 20,
            "amr_speed": 1.0,
            "amr_width": 0.8,
            "amr_length": 1.2,
            "amrs": [{"id": "amr-1", "start_node": "n1"}],
        },
    )

    config = load_config(path)

    assert config.beacon_port == 9999


def test_load_config_honors_explicit_beacon_port(tmp_path):
    path = write_config(
        tmp_path,
        {
            "map": "maps/sample_map.json",
            "port": 8000,
            "tick_hz": 20,
            "amr_speed": 1.0,
            "amr_width": 0.8,
            "amr_length": 1.2,
            "beacon_port": 12345,
            "amrs": [{"id": "amr-1", "start_node": "n1"}],
        },
    )

    config = load_config(path)

    assert config.beacon_port == 12345


def test_load_config_defaults_beacon_interval_to_one_second(tmp_path):
    path = write_config(
        tmp_path,
        {
            "map": "maps/sample_map.json",
            "port": 8000,
            "tick_hz": 20,
            "amr_speed": 1.0,
            "amr_width": 0.8,
            "amr_length": 1.2,
            "amrs": [{"id": "amr-1", "start_node": "n1"}],
        },
    )

    config = load_config(path)

    assert config.beacon_interval_s == 1.0


def test_load_config_allows_empty_amr_list(tmp_path):
    """An empty amrs list is valid — AMRs are now added dynamically at registration time."""
    path = write_config(
        tmp_path,
        {
            "map": "maps/sample_map.json",
            "port": 8000,
            "tick_hz": 20,
            "amr_speed": 1.0,
            "amr_width": 0.8,
            "amr_length": 1.2,
            "amrs": [],
        },
    )

    config = load_config(path)
    assert config.amrs == []
