import asyncio
import math
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import networkx as nx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.beacon import BeaconPublisher, UDPNetworkManager, publish_snapshot
from backend.config import Config, load_config
from backend.map import load_map
from backend.simulation import Simulation

VIEWER_DIST_DIR = "viewer/dist"


class OrderRequest(BaseModel):
    amr_id: str
    target_node: str


class TaskCreateRequest(BaseModel):
    task_id: Optional[str] = None
    pickup_node: str
    dropoff_node: str
    priority: int = 1


class AddNodeRequest(BaseModel):
    id: str
    x: float
    y: float
    type: Optional[str] = "dock"  # "dock" | "charging" | "aisle"


class AddEdgeRequest(BaseModel):
    from_node: str
    to_node: str
    bidirectional: bool = True


class LogoutRequest(BaseModel):
    email: str
    action: Optional[str] = "despawn"  # "despawn" | "dock"


class AMRRegisterRequest(BaseModel):
    """Registration payload sent by the browser when a new user enters."""
    name: str
    email: str
    password: str
    start_node: str


class LoginRequest(BaseModel):
    email: str
    password: str


class SpawnAMRRequest(BaseModel):
    email: Optional[str] = "guest"
    start_node: str


class RenameAMRRequest(BaseModel):
    name: str



def build_app(config: Config) -> FastAPI:
    """Build the FastAPI app: REST endpoints, WebSocket broadcast, tick loop, and P2P UDP mesh."""
    graph = load_map(config.map)
    simulation = Simulation(
        graph=graph,
        amr_configs=config.amrs,
        speed=config.amr_speed,
        width=config.amr_width,
        length=config.amr_length,
    )
    dt = 1.0 / config.tick_hz
    beacon_publisher = BeaconPublisher(port=config.beacon_port)

    # Initialize Decentralized P2P UDP Mesh Manager
    network_manager: Optional[UDPNetworkManager] = None
    if getattr(config, "p2p_mesh_enabled", True):
        network_manager = UDPNetworkManager(
            port=config.beacon_port,
            fleet_prefix=getattr(config, "fleet_prefix", ""),
            peer_ips=getattr(config, "peer_ips", []),
        )
        simulation.network_manager = network_manager

    active_websockets: list[WebSocket] = []

    async def tick_loop() -> None:
        while True:
            simulation.step(dt)
            if active_websockets:
                snap = simulation.snapshot()
                for ws in list(active_websockets):
                    try:
                        await ws.send_json(snap)
                    except Exception:
                        if ws in active_websockets:
                            active_websockets.remove(ws)
            await asyncio.sleep(dt)

    async def beacon_loop() -> None:
        warned: set[str] = set()
        while True:
            publish_snapshot(beacon_publisher, simulation.snapshot(), warned)
            await asyncio.sleep(config.beacon_interval_s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tick_task = asyncio.create_task(tick_loop())
        beacon_task = asyncio.create_task(beacon_loop())
        listener_task = None
        gossip_task = None

        if network_manager:
            listener_task = asyncio.create_task(
                network_manager.listen_loop(simulation.handle_network_packet)
            )
            gossip_task = asyncio.create_task(
                network_manager.round_robin_gossip_loop(simulation, tick_interval=dt)
            )

        try:
            yield
        finally:
            tick_task.cancel()
            beacon_task.cancel()
            if listener_task:
                listener_task.cancel()
            if gossip_task:
                gossip_task.cancel()
            beacon_publisher.close()
            if network_manager:
                network_manager.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        sb_url = os.environ.get("SUPABASE_URL", "").strip()
        sb_connected = False
        if sb_url:
            try:
                from backend.db import _get_client
                client = _get_client()
                client.table("profiles").select("count", count="exact").limit(1).execute()
                sb_connected = True
            except Exception:
                pass
        return {
            "status": "ok",
            "supabase_connected": sb_connected,
            "supabase_url": sb_url if sb_connected else None,
        }

    @app.get("/api/amrs")
    @app.get("/api/simulation/state")
    def get_amrs_state() -> list[dict]:
        """Return current real-time state and telemetry for all active AMRs."""
        return simulation.snapshot()

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """High-speed 20 Hz WebSocket telemetry stream for 3D digital twin."""
        await websocket.accept()
        active_websockets.append(websocket)
        try:
            await websocket.send_json(simulation.snapshot())
            while True:
                await websocket.receive_text()
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            if websocket in active_websockets:
                active_websockets.remove(websocket)

    @app.get("/api/map")
    def get_map() -> dict:
        """Return dynamic warehouse graph nodes, edges, charging docks, and robot dimensions for 3D digital twin."""
        nodes = [
            {
                "id": n,
                "x": graph.nodes[n].get("x", 0.0),
                "y": graph.nodes[n].get("y", 0.0),
                "type": graph.nodes[n].get("type", "aisle"),
            }
            for n in graph.nodes
        ]
        edges = [{"source": u, "target": v, "from": u, "to": v} for u, v in graph.edges]
        charging_node = simulation.get_charging_node()
        charging_nodes = simulation.get_charging_nodes()

        return {
            "nodes": nodes,
            "edges": edges,
            "charging_node": charging_node,
            "charging_nodes": charging_nodes,
            "amr_width": simulation.width,
            "amr_length": simulation.length,
            "amr_speed": simulation.speed,
        }

    @app.post("/api/map/nodes")
    def add_map_node(req: AddNodeRequest) -> dict:
        """Dynamically add a new station node to the warehouse map."""
        node_id = req.id.strip()
        if not node_id:
            raise HTTPException(status_code=422, detail="Node ID is required.")
        if node_id in graph.nodes:
            raise HTTPException(status_code=400, detail=f"Station '{node_id}' already exists in map.")
        graph.add_node(node_id, x=float(req.x), y=float(req.y), type=req.type or "dock")
        return {
            "status": "ok",
            "node": {"id": node_id, "x": req.x, "y": req.y, "type": req.type or "dock"},
            "message": f"Station '{node_id}' added to warehouse graph.",
        }

    @app.delete("/api/map/nodes/{node_id}")
    def delete_map_node(node_id: str) -> dict:
        """Dynamically remove a station node and its incident corridors from the map."""
        if node_id not in graph.nodes:
            raise HTTPException(status_code=404, detail=f"Station '{node_id}' not found.")
        occupied = simulation.get_occupied_nodes()
        if node_id in occupied:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete Station '{node_id}' because an active AMR is currently occupying it.",
            )
        graph.remove_node(node_id)
        return {"status": "ok", "deleted_node": node_id}

    @app.post("/api/map/edges")
    def add_map_edge(req: AddEdgeRequest) -> dict:
        """Dynamically connect a travel corridor between two warehouse stations."""
        u, v = req.from_node.strip(), req.to_node.strip()
        if u not in graph.nodes or v not in graph.nodes:
            raise HTTPException(status_code=404, detail=f"Both stations must exist in map. ({u}, {v})")
        dx = graph.nodes[v]["x"] - graph.nodes[u]["x"]
        dy = graph.nodes[v]["y"] - graph.nodes[u]["y"]
        dist = math.hypot(dx, dy)
        graph.add_edge(u, v, weight=dist)
        if req.bidirectional:
            graph.add_edge(v, u, weight=dist)
        return {
            "status": "ok",
            "edge": {"from": u, "to": v, "weight": dist, "bidirectional": req.bidirectional},
            "message": f"Corridor connected: {u} <-> {v}",
        }

    @app.post("/api/map/shuffle")
    def shuffle_map_layout() -> dict:
        """Procedurally regenerate and shuffle the warehouse layout while safely repositioning active AMRs."""
        from backend.map import generate_procedural_map

        # 1. Generate new procedural warehouse graph preserving authentic complex corridors
        new_g = generate_procedural_map(variation_scale=0.3)

        # 2. Clear and update active graph in place
        graph.clear()
        for n, d in new_g.nodes(data=True):
            graph.add_node(n, **d)
        for u, v, d in new_g.edges(data=True):
            graph.add_edge(u, v, **d)

        simulation.graph = graph

        # 3. Safely re-assign active AMRs to distinct valid nodes in the new layout
        available_nodes = list(graph.nodes)
        for idx, (amr_id, amr) in enumerate(simulation.amrs.items()):
            assigned_node = available_nodes[idx % len(available_nodes)]
            amr.current_node = assigned_node
            node_data = graph.nodes[assigned_node]
            amr.x = float(node_data["x"])
            amr.y = float(node_data["y"])
            amr.progress = 0.0
            amr.path = []
            amr.state_label = "IDLE"
            if amr.parasite:
                amr.parasite.graph = graph
                amr.parasite.cbba.graph = graph
                amr.parasite.active_task_id = None
                amr.parasite.active_subtask = None
                amr.parasite.cbba.state.bundle.clear()
                amr.parasite.cbba.state.path.clear()

        # Reset space-time reservations
        if hasattr(simulation.traffic_manager, "reservation_table"):
            simulation.traffic_manager.reservation_table.table.clear()

        return {
            "status": "ok",
            "message": f"Warehouse map shuffled successfully ({len(graph.nodes)} stations, {len(graph.edges)} corridors).",
            "total_nodes": len(graph.nodes),
            "total_edges": len(graph.edges),
            "charging_node": simulation.get_charging_node(),
        }

    @app.post("/api/auth/logout")
    def logout_user(req: LogoutRequest) -> dict:
        """Handle user session close: cleanly docks or despawns user AMRs."""
        import re
        clean_prefix = re.sub(r"[^a-zA-Z0-9_]", "_", req.email.split("@")[0])
        user_amrs = [a_id for a_id in list(simulation.amrs.keys()) if clean_prefix in a_id]

        if req.action == "dock":
            home_node = simulation.get_charging_node()
            for a_id in user_amrs:
                amr = simulation.amrs.get(a_id)
                if amr:
                    try:
                        simulation.set_order(a_id, home_node)
                    except Exception:
                        pass
            return {"status": "ok", "action": "dock", "affected_amrs": user_amrs}
        else:
            for a_id in user_amrs:
                simulation.remove_amr(a_id)
            return {"status": "ok", "action": "despawn", "removed_amrs": user_amrs}


    @app.post("/api/auth/login")
    def login_user(req: LoginRequest) -> dict:
        """Authenticate returning user and retrieve their associated AMRs."""
        email = req.email.strip().lower()
        if not email or "@" not in email:
            raise HTTPException(status_code=422, detail="A valid email is required.")
        if not req.password:
            raise HTTPException(status_code=422, detail="Password is required.")

        import re
        clean_prefix = re.sub(r"[^a-zA-Z0-9_]", "_", email.split("@")[0])
        user_amrs = [a_id for a_id in simulation.amrs.keys() if clean_prefix in a_id]
        if not user_amrs:
            primary_amr = f"amr_{clean_prefix}_1"
            occupied_nodes = simulation.get_occupied_nodes()
            vacant_nodes = [n for n in graph.nodes if n not in occupied_nodes]
            fallback_node = vacant_nodes[0] if vacant_nodes else next(iter(graph.nodes), "n1")
            try:
                simulation.add_amr(primary_amr, fallback_node)
            except ValueError:
                pass
            user_amrs = [primary_amr]

        user_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, email))
        return {
            "status": "ok",
            "user_id": user_id,
            "email": email,
            "name": clean_prefix.replace("_", " ").title(),
            "amr_id": user_amrs[0],
            "amrs": user_amrs,
        }

    @app.post("/api/amrs/spawn")
    def spawn_amr(req: SpawnAMRRequest) -> dict:
        """Spawn an additional AMR for the user (Max 6 per account) strictly at an unoccupied node."""
        email = (req.email or "guest").strip().lower()
        import re
        clean_prefix = re.sub(r"[^a-zA-Z0-9_]", "_", email.split("@")[0])
        existing_user_amrs = [a_id for a_id in simulation.amrs.keys() if clean_prefix in a_id]

        MAX_USER_AMRS = 6
        if len(existing_user_amrs) >= MAX_USER_AMRS:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum limit reached: You can spawn up to {MAX_USER_AMRS} AMRs per account.",
            )

        total_nodes = len(graph.nodes)
        if len(simulation.amrs) >= max(1, total_nodes - 1):
            raise HTTPException(
                status_code=400,
                detail=f"Warehouse at maximum density capacity ({len(simulation.amrs)}/{total_nodes} stations occupied). Despawn an AMR to free station headroom.",
            )

        occupied_nodes = simulation.get_occupied_nodes()
        start_node = req.start_node.strip()

        # Strict collision-free check: Never spawn on an already occupied node!
        if start_node not in graph.nodes or start_node in occupied_nodes:
            vacant_nodes = [n for n in graph.nodes if n not in occupied_nodes]
            if not vacant_nodes:
                raise HTTPException(
                    status_code=400,
                    detail="All warehouse stations are currently occupied. Cannot spawn another AMR.",
                )
            start_node = vacant_nodes[0]

        new_index = len(existing_user_amrs) + 1
        new_amr_id = f"amr_{clean_prefix}_{new_index}"
        while new_amr_id in simulation.amrs:
            new_index += 1
            new_amr_id = f"amr_{clean_prefix}_{new_index}"

        try:
            simulation.add_amr(new_amr_id, start_node)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        return {
            "status": "ok",
            "amr_id": new_amr_id,
            "start_node": start_node,
            "total_amrs": len(existing_user_amrs) + 1,
            "amrs": existing_user_amrs + [new_amr_id],
        }

    @app.delete("/api/amrs/{amr_id}")
    @app.post("/api/amrs/{amr_id}/despawn")
    @app.post("/api/amrs/{amr_id}/remove")
    def remove_amr_endpoint(amr_id: str) -> dict:
        """Decommission and remove an AMR from the active fleet, freeing its station and tasks."""
        success = simulation.remove_amr(amr_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"AMR '{amr_id}' not found.")
        return {
            "status": "ok",
            "removed_amr_id": amr_id,
            "message": f"AMR '{amr_id}' successfully decommissioned and station freed.",
        }

    @app.post("/api/amrs/{amr_id}/rename")
    @app.put("/api/amrs/{amr_id}/rename")
    @app.patch("/api/amrs/{amr_id}/rename")
    def rename_amr_endpoint(amr_id: str, req: RenameAMRRequest) -> dict:

        """Update an AMR's display name locally and persist to Supabase."""
        new_name = req.name.strip()
        if not new_name:
            raise HTTPException(status_code=422, detail="AMR name cannot be empty.")
        if amr_id not in simulation.amrs:
            raise HTTPException(status_code=404, detail=f"AMR '{amr_id}' not found in active fleet.")
        
        # 1. Update in-memory simulation
        simulation.rename_amr(amr_id, new_name)

        # 2. Persist to Supabase
        db_persisted = False
        try:
            from backend.db import rename_amr_in_db
            db_persisted = rename_amr_in_db(amr_id, new_name)
        except Exception:
            pass

        return {
            "status": "ok",
            "amr_id": amr_id,
            "name": new_name,
            "persisted_to_supabase": db_persisted,
        }


    @app.post("/api/amrs/register")
    def register_amr(req: AMRRegisterRequest) -> dict:
        """
        Register a new user and dynamically add their AMR to the shared simulation.

        Flow:
          1. Validate inputs.
          2. Create (or retrieve) a Supabase Auth user to obtain a UUID —
             the password is only used here and never stored manually.
          3. Persist profile + AMR rows in Supabase via the db module.
          4. Insert the AMR into the running simulation.
          5. Return profile, amr_id, and a minimal session token for the frontend.
        """
        import os as _os

        # --- Input validation ---
        name = req.name.strip()
        email = req.email.strip().lower()
        start_node = req.start_node.strip()

        if not name:
            raise HTTPException(status_code=422, detail="Name is required.")
        if not email or "@" not in email:
            raise HTTPException(status_code=422, detail="A valid email is required.")
        if not req.password or len(req.password) < 6:
            raise HTTPException(status_code=422, detail="Password must be at least 6 characters.")
        if start_node not in graph.nodes:
            known = sorted(graph.nodes)
            raise HTTPException(
                status_code=422,
                detail=f"Unknown start node '{start_node}'. Valid nodes: {known}",
            )

        # --- Supabase auth + database ---
        try:
            from backend.db import create_profile_and_amr, get_profile_by_email, get_amr_by_user_id
            supabase_url = _os.environ.get("SUPABASE_URL", "")
            supabase_key = _os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
            if not supabase_url or not supabase_key:
                raise HTTPException(
                    status_code=503,
                    detail="Registration service is not configured. "
                           "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set on the server.",
                )

            from supabase import create_client as _create_client
            _sb = _create_client(supabase_url, supabase_key)

            # Try to create auth user; if it already exists, sign in to get the id.
            auth_user_id: str | None = None
            try:
                sign_up_resp = _sb.auth.admin.create_user({
                    "email": email,
                    "password": req.password,
                    "email_confirm": True,
                })
                auth_user_id = sign_up_resp.user.id
            except Exception as sign_up_err:
                err_msg = str(sign_up_err).lower()
                if "already registered" in err_msg or "already exists" in err_msg or "duplicate" in err_msg:
                    # User already has an auth account; look up existing profile
                    existing_profile = get_profile_by_email(email)
                    if existing_profile:
                        existing_amr = get_amr_by_user_id(existing_profile["id"])
                        existing_amr_id = existing_amr["amr_id"] if existing_amr else None
                        # If their AMR is already in the simulation, return it
                        if existing_amr_id and existing_amr_id in simulation.amrs:
                            return {
                                "status": "ok",
                                "already_registered": True,
                                "user_id": existing_profile["id"],
                                "amr_id": existing_amr_id,
                                "profile": existing_profile,
                                "amr": existing_amr,
                            }
                        # AMR not yet in simulation – re-insert it
                        if existing_amr_id and existing_amr_id not in simulation.amrs:
                            stored_node = existing_amr.get("start_node", start_node)
                            if stored_node not in graph.nodes:
                                stored_node = start_node
                            try:
                                simulation.add_amr(existing_amr_id, stored_node)
                            except ValueError:
                                pass  # already added by a concurrent request
                        return {
                            "status": "ok",
                            "already_registered": True,
                            "user_id": existing_profile["id"],
                            "amr_id": existing_amr_id,
                            "profile": existing_profile,
                            "amr": existing_amr,
                        }
                raise HTTPException(
                    status_code=400,
                    detail="Could not create account. Please check your email and try again.",
                ) from sign_up_err

            # --- Persist profile + AMR to Supabase ---
            db_result = create_profile_and_amr(
                auth_user_id=auth_user_id,
                name=name,
                email=email,
                start_node=start_node,
            )

        except HTTPException:
            raise
        except RuntimeError as db_err:
            raise HTTPException(status_code=502, detail="Database error during registration.") from db_err
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Registration failed unexpectedly.") from exc

        profile_row = db_result["profile"]
        amr_row = db_result["amr"]
        amr_id = amr_row["amr_id"]

        # --- Insert AMR into the live simulation ---
        try:
            simulation.add_amr(amr_id, start_node)
        except ValueError as ve:
            # Duplicate amr_id means a concurrent request beat us; that's fine.
            if "already exists" not in str(ve):
                raise HTTPException(status_code=500, detail=str(ve)) from ve

        return {
            "status": "ok",
            "already_registered": False,
            "user_id": auth_user_id,
            "amr_id": amr_id,
            "profile": profile_row,
            "amr": amr_row,
        }

    @app.get("/api/map")
    def get_map() -> dict:
        nodes = [{"id": n, "x": d["x"], "y": d["y"]} for n, d in graph.nodes(data=True)]
        edges = [{"from": u, "to": v} for u, v in graph.edges()]
        return {
            "nodes": nodes,
            "edges": edges,
            "amr_width": config.amr_width,
            "amr_length": config.amr_length,
            "amr_speed": config.amr_speed,
        }

    @app.post("/api/orders")
    def post_order(order: OrderRequest) -> dict:
        if order.amr_id not in simulation.amrs:
            raise HTTPException(status_code=404, detail=f"unknown amr_id: {order.amr_id}")
        if order.target_node not in graph.nodes:
            raise HTTPException(
                status_code=404, detail=f"unknown target_node: {order.target_node}"
            )
        try:
            simulation.set_order(order.amr_id, order.target_node)
        except nx.NetworkXNoPath:
            raise HTTPException(status_code=422, detail="no path to target_node") from None
        return {"status": "ok"}

    @app.post("/api/tasks")
    def create_task(req: TaskCreateRequest) -> dict:
        tid = req.task_id or f"task-{uuid.uuid4().hex[:6]}"
        # Check if pickup or dropoff station is currently occupied by a disabled/failed AMR
        for amr in simulation.amrs.values():
            if amr.state_label == "FAILED" or (amr.parasite and not amr.parasite.is_alive):
                if req.pickup_node == amr.current_node:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Pickup Station {req.pickup_node} is physically blocked by disabled robot {amr.id}! Recover robot first.",
                    )
                if req.dropoff_node == amr.current_node:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Dropoff Station {req.dropoff_node} is physically blocked by disabled robot {amr.id}! Recover robot first.",
                    )

        try:
            task = simulation.add_task(
                task_id=tid,
                pickup_node=req.pickup_node,
                dropoff_node=req.dropoff_node,
                priority=req.priority,
            )
            return {"status": "ok", "task": task.to_dict()}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/tasks")
    def get_tasks() -> list[dict]:
        return [t.to_dict() for t in simulation.tasks.values()]

    @app.get("/api/tasks/history")
    def get_tasks_history() -> list[dict]:
        """Retrieve full chronological task execution history including completed and in-flight missions."""
        return simulation.get_task_history()

    @app.post("/api/tasks/clear")
    @app.delete("/api/tasks")
    def clear_tasks_endpoint(include_active: bool = False) -> dict:
        """Clear completed or all tasks from the simulation task pool."""
        count = simulation.clear_tasks(include_active=include_active)
        return {"status": "ok", "cleared_count": count}

    @app.delete("/api/tasks/{task_id}")
    def cancel_task_endpoint(task_id: str) -> dict:
        """Cancel or remove a specific task from the active queue."""
        if task_id in simulation.tasks:
            t = simulation.tasks[task_id]
            t.status = TaskStatus.FAILED
            simulation._record_task_history(t)
            for amr in simulation.amrs.values():
                if amr.parasite:
                    if task_id in amr.parasite.cbba.state.bundle:
                        amr.parasite.cbba.state.bundle = [
                            b for b in amr.parasite.cbba.state.bundle if b != task_id
                        ]
                    if amr.parasite.active_task_id == task_id:
                        amr.parasite.active_task_id = None
                        amr.parasite.active_subtask = None
                        amr.path = []
                        amr.state_label = "IDLE"
            simulation.tasks.pop(task_id, None)
            return {"status": "ok", "cancelled_task": task_id}
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")

    @app.get("/api/cbba/state")
    def get_cbba_state() -> dict:
        return simulation.get_cbba_state()

    @app.post("/api/nodes/{amr_id}/kill")
    def kill_node(amr_id: str) -> dict:
        if amr_id not in simulation.amrs:
            raise HTTPException(status_code=404, detail=f"unknown amr_id: {amr_id}")
        simulation.kill_node(amr_id)
        return {"status": "ok", "message": f"AMR {amr_id} killed"}

    @app.post("/api/nodes/{amr_id}/recover")
    def recover_node(amr_id: str) -> dict:
        if amr_id not in simulation.amrs:
            raise HTTPException(status_code=404, detail=f"unknown amr_id: {amr_id}")
        simulation.recover_node(amr_id)
        return {"status": "ok", "message": f"AMR {amr_id} recovered"}

    @app.post("/api/nodes/{amr_id}/charge")
    def charge_node(amr_id: str) -> dict:
        if amr_id not in simulation.amrs:
            raise HTTPException(status_code=404, detail=f"unknown amr_id: {amr_id}")
        amr = simulation.amrs[amr_id]
        if amr.parasite:
            amr.parasite.battery_soc = 15.0  # Trigger low-battery return to charge pad
        return {"status": "ok", "message": f"AMR {amr_id} dispatched to charging bay"}

    @app.get("/api/amrs")
    def get_amrs() -> list[dict]:
        return simulation.snapshot()

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(simulation.snapshot())
                await asyncio.sleep(dt)
        except WebSocketDisconnect:
            pass

    if os.path.isdir(VIEWER_DIST_DIR):
        from fastapi.responses import FileResponse

        index_file = os.path.join(VIEWER_DIST_DIR, "index.html")

        @app.get("/monitor")
        def serve_monitor():
            return FileResponse(index_file)

        app.mount("/", StaticFiles(directory=VIEWER_DIST_DIR, html=True), name="viewer")
    else:
        print(
            f"server: {VIEWER_DIST_DIR} not found, GUI will not be served "
            "(run `npm run build` in viewer/, or use `npm run dev` separately)"
        )

    return app


# Default module-level application instance for uvicorn runner
def _get_default_app() -> FastAPI:
    import os
    cfg_file = os.environ.get("SIM_CONFIG_PATH", "config/sim_config.json")
    if os.path.exists(cfg_file):
        cfg = load_config(cfg_file)
    else:
        cfg = Config(
            map="maps/default.json",
            port=int(os.environ.get("PORT", 8000)),
            tick_hz=30,
            amr_speed=1.5,
            amr_width=0.6,
            amr_length=0.8,
            amrs=[],
        )
    return build_app(cfg)


app = _get_default_app()

