import asyncio
import os
from contextlib import asynccontextmanager

import networkx as nx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.beacon import BeaconPublisher, publish_snapshot
from backend.config import Config
from backend.map import load_map
from backend.simulation import Simulation

VIEWER_DIST_DIR = "viewer/dist"


class OrderRequest(BaseModel):
    amr_id: str
    target_node: str


def build_app(config: Config) -> FastAPI:
    """Build the FastAPI app: REST endpoints, WebSocket broadcast, tick loop."""
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
        try:
            yield
        finally:
            tick_task.cancel()
            beacon_task.cancel()
            beacon_publisher.close()

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
        app.mount("/", StaticFiles(directory=VIEWER_DIST_DIR, html=True), name="viewer")
    else:
        print(
            f"server: {VIEWER_DIST_DIR} not found, GUI will not be served "
            "(run `npm run build` in viewer/, or use `npm run dev` separately)"
        )

    return app
