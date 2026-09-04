import sys
import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

from backend.config import load_config
from backend.server import build_app


def check_supabase_connection() -> tuple[bool, str]:
    """Test whether Supabase cloud database is reachable."""
    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    if not sb_url:
        return False, "SUPABASE_URL environment variable is missing."
    try:
        from backend.db import _get_client
        client = _get_client()
        client.table("profiles").select("count", count="exact").limit(1).execute()
        return True, f"Connected to {sb_url}"
    except Exception as e:
        return False, str(e)


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/sim_config.json"
    config = load_config(config_path)

    sb_connected, sb_detail = check_supabase_connection()

    print("\n" + "=" * 70)
    print(" >>> AMR FLEET MANAGEMENT & DECENTRALIZED CBBA SYSTEM <<<")
    print("=" * 70)
    print(f" * Simulation Server: http://{config.host}:{config.port}")
    print(f" * Fleet Prefix:     '{config.fleet_prefix}'")
    print(f" * P2P UDP Port:     {config.beacon_port}")
    if sb_connected:
        print(f" * Supabase Cloud:   [CONNECTED] ({sb_detail})")
    else:
        print(f" * Supabase Cloud:   [OFFLINE / NOT CONNECTED] ({sb_detail})")
    print("=" * 70 + "\n")

    app = build_app(config)
    uvicorn.run(app, host=config.host, port=config.port, timeout_graceful_shutdown=2)
