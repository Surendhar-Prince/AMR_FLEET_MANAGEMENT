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
    assert response.json()["status"] == "ok"


def test_get_map_returns_nodes_and_edges():
    client = make_client()

    response = client.get("/api/map")
    body = response.json()

    assert response.status_code == 200
    assert any(n["id"] == "n1" and n["x"] == 0.0 and n["y"] == 0.0 for n in body["nodes"])
    assert any((e.get("from") == "n1" and e.get("to") == "n2") or (e.get("source") == "n1" and e.get("target") == "n2") for e in body["edges"])
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

    # 2. Get tasks list and history
    res_list = client.get("/api/tasks")
    assert res_list.status_code == 200
    tasks = res_list.json()
    assert len(tasks) == 1
    assert tasks[0]["id"] == task_data["id"]

    res_hist = client.get("/api/tasks/history")
    assert res_hist.status_code == 200
    history = res_hist.json()
    assert len(history) == 1
    assert history[0]["id"] == task_data["id"]

    # 3. Create task with invalid node
    res_err = client.post("/api/tasks", json={"pickup_node": "invalid-node", "dropoff_node": "n3"})
    assert res_err.status_code == 400

    # 4. Clear tasks
    res_clear = client.post("/api/tasks/clear?include_active=true")
    assert res_clear.status_code == 200
    assert res_clear.json()["cleared_count"] >= 1


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


def test_dynamic_map_and_logout_api():
    client = make_client()

    # 1. Add new station dynamically
    res_node = client.post("/api/map/nodes", json={"id": "n99", "x": 25.0, "y": 12.0, "type": "dock"})
    assert res_node.status_code == 200
    assert res_node.json()["status"] == "ok"

    # 2. Add corridor dynamically
    res_edge = client.post("/api/map/edges", json={"from_node": "n1", "to_node": "n99", "bidirectional": True})
    assert res_edge.status_code == 200
    assert res_edge.json()["status"] == "ok"

    # 3. Verify in /api/map
    map_res = client.get("/api/map").json()
    node_ids = [n["id"] for n in map_res["nodes"]]
    assert "n99" in node_ids

    # 4. Delete station
    del_res = client.delete("/api/map/nodes/n99")
    assert del_res.status_code == 200

    # 5. Test logout user
    logout_res = client.post("/api/auth/logout", json={"email": "operator@test.com", "action": "despawn"})
    assert logout_res.status_code == 200
    assert logout_res.json()["status"] == "ok"

    # 6. Test 1-click map shuffle
    shuffle_res = client.post("/api/map/shuffle")
    assert shuffle_res.status_code == 200
    assert shuffle_res.json()["status"] == "ok"
    assert shuffle_res.json()["total_nodes"] >= 10


