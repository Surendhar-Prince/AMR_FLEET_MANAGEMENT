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


def test_monitor_serves_built_viewer_when_dist_exists():
    client = make_client()

    response = client.get("/monitor")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_post_order_unknown_target_node_returns_404():
    client = make_client()

    response = client.post("/api/orders", json={"amr_id": "amr-1", "target_node": "no-such-node"})

    assert response.status_code == 404


def test_tasks_lifecycle_api():
    client = make_client()

    # 1. Create a valid task
    res = client.post("/api/tasks", json={"pickup_node": "n1", "dropoff_node": "n3", "priority": 2})
    assert res.status_code == 200
    task_data = res.json()["task"]
    assert task_data["pickup_node"] == "n1"
    assert task_data["dropoff_node"] == "n3"
    assert task_data["priority"] == 2

    # 2. Get tasks list
    res_list = client.get("/api/tasks")
    assert res_list.status_code == 200
    tasks = res_list.json()
    assert len(tasks) == 1
    assert tasks[0]["id"] == task_data["id"]

    # 3. Create task with invalid node
    res_err = client.post("/api/tasks", json={"pickup_node": "invalid-node", "dropoff_node": "n3"})
    assert res_err.status_code == 400


def test_cbba_state_api():
    client = make_client()

    client.post("/api/tasks", json={"task_id": "test-task", "pickup_node": "n1", "dropoff_node": "n2", "priority": 1})
    res = client.get("/api/cbba/state")
    assert res.status_code == 200
    state = res.json()
    assert "tasks" in state
    assert "nodes" in state
    assert "bid_matrix" in state
    assert "network_telemetry" in state
    assert len(state["tasks"]) == 1
    assert "amr-1" in state["nodes"]


def test_kill_recover_charge_node_api():
    client = make_client()

    # Kill amr-1
    res_kill = client.post("/api/nodes/amr-1/kill")
    assert res_kill.status_code == 200
    assert res_kill.json()["status"] == "ok"

    # Attempt to create task on node blocked by killed amr-1 (which is at n1)
    res_blocked = client.post("/api/tasks", json={"pickup_node": "n1", "dropoff_node": "n3"})
    assert res_blocked.status_code == 400
    assert "physically blocked" in res_blocked.json()["detail"]

    # Recover amr-1
    res_rec = client.post("/api/nodes/amr-1/recover")
    assert res_rec.status_code == 200

    # Charge amr-1
    res_charge = client.post("/api/nodes/amr-1/charge")
    assert res_charge.status_code == 200

    # 404 for unknown AMR
    res_404 = client.post("/api/nodes/unknown-amr/kill")
    assert res_404.status_code == 404

