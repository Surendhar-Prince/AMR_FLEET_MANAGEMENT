"""Bulk Task Generator for AMR Fleet Management.
Injects a batch of warehouse pickup/dropoff tasks and displays live CBBA bidding results.
"""
import time
import httpx

SERVER_URL = "http://localhost:8000"

SAMPLE_BULK_TASKS = [
    {"task_id": "ORDER-101", "pickup_node": "n1", "dropoff_node": "n4", "priority": 3},
    {"task_id": "ORDER-102", "pickup_node": "n3", "dropoff_node": "n10", "priority": 2},
    {"task_id": "ORDER-103", "pickup_node": "n7", "dropoff_node": "n14", "priority": 2},
    {"task_id": "ORDER-104", "pickup_node": "n12", "dropoff_node": "n2", "priority": 1},
]


def main():
    print("==========================================================")
    print("🚀 INJECTING BULK TASKS INTO DECENTRALIZED POOL")
    print("==========================================================")

    client = httpx.Client(base_url=SERVER_URL, timeout=5.0)

    # 1. Check server health
    try:
        res = client.get("/api/health")
        if res.status_code != 200:
            print("❌ Server is not responding. Please make sure `python main.py` is running.")
            return
    except Exception as e:
        print(f"❌ Cannot connect to {SERVER_URL}: {e}")
        print("Please start the backend server in another terminal: `python main.py`")
        return

    # 2. Inject bulk tasks
    print(f"\n📦 Submitting {len(SAMPLE_BULK_TASKS)} warehouse tasks simultaneously...")
    for t in SAMPLE_BULK_TASKS:
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
