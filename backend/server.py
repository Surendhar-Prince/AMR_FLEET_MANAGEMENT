import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import networkx as nx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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
            port=config.beacon_port, fleet_prefix=getattr(config, "fleet_prefix", "")
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

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

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
