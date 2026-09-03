import asyncio
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
from backend.config import Config
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


class AMRRegisterRequest(BaseModel):
    """Registration payload sent by the browser when a new user enters."""
    name: str
    email: str
    # password is only used to create the Supabase auth identity and is
    # never stored anywhere else.
    password: str
    start_node: str


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

    async def tick_loop() -> None:
        while True:
            simulation.step(dt)
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
        allow_origins=[
            "https://amr-fleet-management.vercel.app",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

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
