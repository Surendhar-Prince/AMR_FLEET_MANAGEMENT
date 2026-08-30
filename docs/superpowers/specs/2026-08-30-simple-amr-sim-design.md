# simple-amr-sim — Design Spec

Date: 2026-08-30
Status: Approved (pending implementation plan)

## Background

`mnmsw-virtual-factory` (at `/Users/ramenada/mnmsw-virtual-factory`) is a full
VDA5050 2.0.0 fleet-manager testbed: multiple AMR manufacturers/models, MQTT +
VDA5050 order protocol, curve-aware map parsing, Pymunk physics with topheads
and chargers, multi-factory config, and a React/Three.js viewer with URDF
robot models (~15.6k lines of Python plus a full frontend).

This project is a from-scratch, drastically smaller simulator that keeps only
the core idea — AMRs moving along a graph map, visualized in a browser — and
drops everything else. It lives in a separate sibling directory rather than
being carved out of the original repo, because the original's pieces are
tightly coupled (VDA5050 state machine, MQTT topics, physics/tophead code all
reference each other) and stripping them in place would touch dozens of files
for no benefit over starting clean.

## Goals

- One generic AMR type. No manufacturer/model naming anywhere in code, config,
  or docs.
- Movement commanded via a REST API. No MQTT, no VDA5050 order/state-machine
  protocol.
- Map is a plain directed graph (nodes with x/y, directed edges) loaded
  directly into `networkx.DiGraph`. No curves, no stations/metadata beyond
  position.
- No physics engine. AMR position is computed by linear interpolation along
  the current graph edge at a constant configured speed.
- Collision detection via simple 2D geometry (oriented rectangle overlap
  between AMR footprints), recomputed every simulation tick — not a physics
  engine, just a status flag.
- No pick/drop, no charging, no topheads, no per-model dimension catalog —
  one configurable footprint size shared by all AMRs.
- Single factory, single map, single server port. No multi-factory registry.
- Multiple AMRs (configurable count), since collision detection between AMRs
  is in scope.
- Keep a 3D browser viewer (React + Three.js), but minimal: box-shaped AMRs,
  no URDF models, no config panel, no order history UI.

## Non-Goals (explicitly out of scope)

- VDA5050 protocol, MQTT, any manufacturer/model catalog
- Curved edges, station metadata, asset resources
- Topheads, chargers, pick/drop actions, any node "action" or dwell behavior
- Physics engine (Pymunk or otherwise), acceleration/deceleration profiles
- Multi-factory config, multi-port routing
- Order queues / multi-waypoint routes (a single target-node order only)

## Architecture

```
REST client / curl        Browser (viewer)
      |                         ^
      | POST /api/orders        | WebSocket /ws (state @ tick_hz)
      v                         |
+---------------------------------------+
|            FastAPI server              |
|  - REST: /api/health, /api/map,        |
|    /api/orders, /api/amrs              |
|  - WS: /ws                             |
+-----------------+----------------------+
                   |
                   v
        +---------------------+
        |   Simulation loop    |  (runs at tick_hz on a background task)
        |  - advance AMRs      |
        |  - collision check   |
        +----------+-----------+
                    |
        +-----------+-----------+
        |                       |
   +---------+           +-------------+
   |  map.py  |           |  amr.py     |
   | networkx |           | AMR state,  |
   | DiGraph, |           | path,       |
   | shortest |           | progress    |
   | _path    |           +-------------+
   +---------+
```

## Directory Layout

```
simple-amr-sim/
  backend/
    map.py            # load map JSON -> networkx.DiGraph; shortest_path helper
    amr.py             # AMR class: id, position, heading, path, progress, colliding
    simulation.py       # tick loop: advance AMRs along path, run collision check
    collision.py         # oriented-rectangle overlap check between AMR footprints
    server.py            # FastAPI app: REST endpoints + WebSocket broadcast
    config.py            # load sim_config.json
  maps/
    sample_map.json      # default map for local dev/tests
  config/
    sim_config.json       # map path, port, tick_hz, amr_speed, footprint, amr list
  viewer/                 # React + Vite + Three.js
    src/
      App.tsx
      Scene.tsx            # draws nodes/edges as lines, AMRs as boxes
      useSimulationState.ts  # WebSocket hook, holds latest state
  tests/
    test_map.py
    test_collision.py
    test_simulation.py
  requirements.txt
  README.md
```

## Data Formats

### Map (`maps/sample_map.json`)

```json
{
  "nodes": [
    {"id": "n1", "x": 0.0, "y": 0.0},
    {"id": "n2", "x": 5.0, "y": 0.0},
    {"id": "n3", "x": 5.0, "y": 5.0}
  ],
  "edges": [
    {"from": "n1", "to": "n2"},
    {"from": "n2", "to": "n3"},
    {"from": "n3", "to": "n1"}
  ]
}
```

Loaded via `map.py`: each node becomes `graph.add_node(id, x=x, y=y)`; each
edge becomes `graph.add_edge(from, to, weight=euclidean_distance)`. Edges are
directed as written — bidirectional travel requires listing both directions.
`nx.shortest_path(graph, source, target, weight="weight")` produces the route
for an order.

### Config (`config/sim_config.json`)

```json
{
  "map": "maps/sample_map.json",
  "port": 8000,
  "tick_hz": 20,
  "amr_speed": 1.0,
  "amr_width": 0.8,
  "amr_length": 1.2,
  "amrs": [
    {"id": "amr-1", "start_node": "n1"},
    {"id": "amr-2", "start_node": "n2"}
  ]
}
```

One footprint size (`amr_width` x `amr_length`) applies to every AMR — there
is no per-model catalog since there is only one AMR type.

## Simulation Loop

Runs as a background task at `tick_hz` (default 20 Hz, matching the
`dt = 1/tick_hz` step):

1. For each AMR with a non-empty `path` (list of node ids remaining):
   - Advance `progress` along the current edge by `amr_speed * dt` (meters).
   - When `progress` reaches the edge length, pop to the next edge in `path`
     and carry over remaining distance.
   - Compute `position = lerp(edge.from.xy, edge.to.xy, progress / edge_length)`
     and `heading = atan2(dy, dx)` of the current edge.
   - When `path` is empty, the AMR is idle at its last node.
2. After all AMRs move, `collision.py` checks every pair: build each AMR's
   oriented rectangle from `(position, heading, amr_width, amr_length)` and
   test overlap (separating axis theorem, 2 rectangles). Set `colliding` on
   both AMRs in a colliding pair; clear it otherwise.
3. Collision is a **status flag only** — it does not stop or reroute AMRs in
   this version. (A stop-on-collision policy is a natural follow-up but
   wasn't asked for here.)

## API

All under a single port (from config, default 8000).

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness check |
| GET | `/api/map` | Returns nodes + edges for the viewer to draw |
| POST | `/api/orders` | Body `{"amr_id": str, "target_node": str}`. Computes shortest path from the AMR's current position and sets it as the active route. 404 if `amr_id` or `target_node` unknown. |
| GET | `/api/amrs` | Snapshot list: `{id, position: {x,y}, heading, path, colliding}` for each AMR |
| WS | `/ws` | Pushes the same snapshot as `/api/amrs`, once per tick |

## Viewer

React + Vite + Three.js, no URDF/asset loading:

- `Scene.tsx`: fetches `/api/map` once on load, draws nodes as small
  spheres/dots and edges as lines (using node x/y as world x/z, y=0 up-axis
  flat plane).
- `useSimulationState.ts`: opens `/ws`, holds the latest AMR snapshot in
  state.
- AMRs render as simple colored boxes sized from the config footprint,
  positioned/rotated from the WebSocket state; box turns red (or similar)
  when `colliding` is true.
- No config panel, no order-history UI, no debug overlays. Order submission
  for manual testing can go through `curl`/REST directly; a minimal "click a
  node to send an order" UI is a reasonable follow-up but not required here.

## Testing

Pytest, no broker/simulator process needed (mirrors the original repo's
`tests/` convention):

- `test_map.py`: map JSON loads into the expected `DiGraph`; `shortest_path`
  returns the correct route on a known small graph.
- `test_collision.py`: overlap math against known rectangle pairs (clearly
  separated, touching, overlapping, rotated).
- `test_simulation.py`: given a path and enough ticks, an AMR's position
  converges to the target node's coordinates.

## Open Follow-Ups (not part of this build)

- Stop-on-collision or reroute behavior (currently: flag only)
- Multi-waypoint orders (currently: single target node only)
- Manual "click to send order" UI in the viewer

## Addendum: LAN Position Beacon (2026-08-30)

Use case: this simulator runs independently on up to 3 laptops on the same
LAN, each with its own AMR(s) with globally-unique `amr_id`s. Every AMR
broadcasts its own position over UDP so any machine on the LAN can observe
all AMRs across all instances without a central server. A separate
standalone client script listens for these broadcasts and prints them.

This intentionally does not use the real ZeroMQ/CZMQ `zbeacon`/Zyre
implementation (that requires the `czmq` C library, which is a much heavier
and less portable install across 3 different laptops/OSes). Instead it
reimplements the same wire behavior — a small JSON payload sent via UDP
broadcast, no broker, no connection setup — using only Python's stdlib
`socket` module.

**Wire format** (JSON, UDP datagram):
```json
{"amr_id": "amr-1", "x": 1.23, "y": 4.56, "goal": "n3"}
```
`goal` is the AMR's final destination node (`path[-1]` from its current
route) or `null` when idle.

**Components** (`backend/beacon.py`):
- `encode_beacon_message` / `decode_beacon_message` — pure JSON encode/decode
  functions.
- `BeaconPublisher(port, broadcast_address)` — wraps a UDP socket with
  `SO_BROADCAST` set; `publish(amr_id, x, y, goal)` sends one datagram per
  call.
- `BeaconListener(port)` — binds a UDP socket on that port;
  `receive(timeout)` returns the next decoded message.

**Wiring**: `config/sim_config.json` gains `beacon_port` (default `9999`,
same value on all 3 laptops so they broadcast to/listen on the same port).
The server's tick loop publishes one beacon per AMR, every tick, after
`simulation.step(dt)`.

**Client**: `beacon_client.py` at the project root — a standalone script
(no FastAPI/simulation dependency) that opens a `BeaconListener` and prints
every message it receives, forever, until interrupted. Run one instance
anywhere on the LAN to see all AMRs from all 3 laptops.
