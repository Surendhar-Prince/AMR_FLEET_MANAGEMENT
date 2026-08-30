from fastapi.testclient import TestClient

from backend.config import Config
from backend.server import build_app


def make_client():
    config = Config(
        map="maps/sample_map.json",
        port=8000,
        tick_hz=20,
        amr_speed=1.0,
        amr_width=0.8,
        amr_length=1.2,
        amrs=[{"id": "amr-1", "start_node": "n1"}, {"id": "amr-2", "start_node": "n3"}],
    )
    app = build_app(config)
    return TestClient(app)


def test_health_returns_ok():
    client = make_client()

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_map_returns_nodes_and_edges():
    client = make_client()

    response = client.get("/api/map")
    body = response.json()

    assert response.status_code == 200
    assert {"id": "n1", "x": 0.0, "y": 0.0} in body["nodes"]
    assert {"from": "n1", "to": "n2"} in body["edges"]
    assert body["amr_width"] == 0.8
    assert body["amr_length"] == 1.2
    assert body["amr_speed"] == 1.0


def test_post_order_sets_amr_path():
    client = make_client()

    response = client.post("/api/orders", json={"amr_id": "amr-1", "target_node": "n3"})

    assert response.status_code == 200
    amrs = {a["id"]: a for a in client.get("/api/amrs").json()}
    assert amrs["amr-1"]["path"] == ["n2", "n3"]


def test_post_order_unknown_amr_returns_404():
    client = make_client()

    response = client.post("/api/orders", json={"amr_id": "no-such-amr", "target_node": "n3"})

    assert response.status_code == 404


def test_root_serves_built_viewer_when_dist_exists():
    client = make_client()

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_post_order_unknown_target_node_returns_404():
    client = make_client()

    response = client.post("/api/orders", json={"amr_id": "amr-1", "target_node": "no-such-node"})

    assert response.status_code == 404
