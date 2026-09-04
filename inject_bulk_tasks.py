"""Bulk Task Generator for AMR Fleet Management.
Injects a batch of warehouse pickup/dropoff tasks and displays live CBBA bidding results.
"""
import argparse
import os
import time
import httpx


def main():
    parser = argparse.ArgumentParser(description="Inject bulk tasks into the AMR fleet CBBA pool.")
    parser.add_argument("--url", "--server", type=str, default=os.environ.get("SERVER_URL", "http://localhost:8000"), help="Backend server URL")
    args = parser.parse_args()

    server_url = args.url.rstrip("/")

    print("==========================================================")
    print("🚀 INJECTING BULK TASKS INTO DECENTRALIZED POOL")
    print(f"📡 Target Server: {server_url}")
    print("==========================================================")

    client = httpx.Client(base_url=server_url, timeout=5.0)

    # 1. Check server health
    try:
        res = client.get("/api/health")
        if res.status_code != 200:
            print(f"❌ Server at {server_url} is not responding with HTTP 200.")
            return
    except Exception as e:
        print(f"❌ Cannot connect to {server_url}: {e}")
        print("Please start the backend server in another terminal: `python main.py`")
        return

    # Fetch dynamic map nodes if available
    map_nodes = []
    try:
        m_res = client.get("/api/map")
        if m_res.status_code == 200:
            map_nodes = [n["id"] for n in m_res.json().get("nodes", [])]
    except Exception:
        pass

    if len(map_nodes) >= 4:
        sample_tasks = [
            {"task_id": "ORDER-101", "pickup_node": map_nodes[0], "dropoff_node": map_nodes[3], "priority": 3},
            {"task_id": "ORDER-102", "pickup_node": map_nodes[2], "dropoff_node": map_nodes[-1], "priority": 2},
            {"task_id": "ORDER-103", "pickup_node": map_nodes[1], "dropoff_node": map_nodes[-2], "priority": 2},
            {"task_id": "ORDER-104", "pickup_node": map_nodes[-3], "dropoff_node": map_nodes[1], "priority": 1},
        ]
    else:
        sample_tasks = [
            {"task_id": "ORDER-101", "pickup_node": "n1", "dropoff_node": "n4", "priority": 3},
            {"task_id": "ORDER-102", "pickup_node": "n3", "dropoff_node": "n10", "priority": 2},
            {"task_id": "ORDER-103", "pickup_node": "n7", "dropoff_node": "n14", "priority": 2},
            {"task_id": "ORDER-104", "pickup_node": "n12", "dropoff_node": "n2", "priority": 1},
        ]

    # 2. Inject bulk tasks
    print(f"\n📦 Submitting {len(sample_tasks)} warehouse tasks simultaneously...")
    for t in sample_tasks:
        resp = client.post("/api/tasks", json=t)
        if resp.status_code == 200:
            print(f"  ✅ Injected {t['task_id']}: Pickup [{t['pickup_node']}] ➔ Dropoff [{t['dropoff_node']}] (Priority: {t['priority']})")
        else:
            print(f"  ⚠️ Error injecting {t['task_id']}: {resp.text}")

    print("\n⏳ Allowing 1.5s for Decentralized CBBA Bidding & Consensus across Parasite Nodes...")
    time.sleep(1.5)


    # 3. Query CBBA Consensus State
    cbba_resp = client.get("/api/cbba/state")
    if cbba_resp.status_code == 200:
        data = cbba_resp.json()
        print("\n==========================================================")
        print("📊 DECENTRALIZED CBBA WINNING ASSIGNMENTS")
        print("==========================================================")
        for task in data.get("tasks", []):
            winner = task.get("assigned_to") or "Bidding in progress"
            print(f"  • Task {task['id']:<10} ➔ Won by: [{winner:<6}] | Status: {task['status']:<12}")

        print("\n🤖 ROBOT FLEET STATUS:")
        for amr_id, node_info in data.get("nodes", {}).items():
            state = node_info.get("state_label", "IDLE")
            active = node_info.get("active_task_id") or "None"
            bundle = node_info.get("bundle", [])
            battery = node_info.get("battery_soc", 100.0)
            print(f"  • {amr_id:<6}: State: {state:<9} | Active Task: {active:<10} | Bundle: {bundle} | Battery: {battery}%")

    print("\n🎉 Look at your 3D viewer (http://localhost:8000) to watch all AMRs driving in real-time!")


if __name__ == "__main__":
    main()
