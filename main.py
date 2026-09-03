import sys
import uvicorn
from dotenv import load_dotenv

load_dotenv()

from backend.config import load_config
from backend.server import build_app

if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/sim_config.json"
    config = load_config(config_path)
    print(f"Starting AMR Fleet simulation with [{config_path}] on port {config.port} (Fleet: '{config.fleet_prefix}')")
    app = build_app(config)
    uvicorn.run(app, host="0.0.0.0", port=config.port, timeout_graceful_shutdown=2)
