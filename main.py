import uvicorn

from backend.config import load_config
from backend.server import build_app

if __name__ == "__main__":
    config = load_config("config/sim_config.json")
    app = build_app(config)
    uvicorn.run(app, host="0.0.0.0", port=config.port, timeout_graceful_shutdown=2)
