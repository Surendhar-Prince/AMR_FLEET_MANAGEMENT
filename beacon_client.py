import argparse

from backend.beacon import BEACON_PORT_DEFAULT, BeaconListener

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Print AMR position beacons from the LAN.")
    parser.add_argument("--port", type=int, default=BEACON_PORT_DEFAULT)
    args = parser.parse_args()

    listener = BeaconListener(port=args.port)
    print(f"Listening for AMR beacons on UDP port {listener.port}...")
    try:
        while True:
            message = listener.receive()
            print(message)
    except KeyboardInterrupt:
        pass
    finally:
        listener.close()
