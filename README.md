# simple-amr-sim

A minimal AMR (autonomous mobile robot) simulator: one generic AMR type,
REST-driven movement, a `networkx` directed-graph map, geometric collision
detection (no physics engine), a 3D browser viewer, and a UDP LAN position
beacon for running multiple instances across machines.

See `docs/superpowers/specs/2026-08-30-simple-amr-sim-design.md` for the full
design.

## Setup (one time)

```bash
# backend
pip install -r requirements.txt

# viewer
cd viewer && npm install && cd ..
```

## Start the server and watch it in the GUI

There are two ways to run it, depending on whether the machine has npm.

**Option A — with npm (development, hot-reload).** Two processes, run from
the `simple-amr-sim/` directory:

```bash
# terminal 1 — backend (REST API + WebSocket + physics/collision loop)
python main.py

# terminal 2 — viewer (3D browser UI, hot-reloads on source changes)
cd viewer && npm run dev
```

Open **http://localhost:3000**. The viewer's dev server proxies `/api` and
`/ws` to the backend on port 8000 (`viewer/vite.config.ts`), so nothing
else needs configuring.

**Option B — no npm needed at runtime.** Build the viewer once on any
machine that *does* have npm:

```bash
cd viewer && npm run build && cd ..
```

That produces `viewer/dist/`. From then on, on any machine — including one
with no Node.js/npm installed at all — the backend serves that built GUI
directly:

```bash
python main.py
```

Open **http://localhost:8000** (note: same port as the backend, no 3000).
This is the right setup for the 3 laptops if npm isn't installed on all of
them: build `viewer/dist/` once and copy the whole `simple-amr-sim/`
directory (dist included) to each laptop — each only needs Python.

If `viewer/dist/` doesn't exist yet, `http://localhost:8000/` returns 404
(the backend logs a note about this on startup) but every `/api/*` endpoint
still works fine — useful for backend-only development.

> `viewer/dist/` is a **snapshot** — after any change to `viewer/src/`,
> re-run `npm run build` for that snapshot to update. If you're actively
> editing the viewer, use Option A instead (hot-reload, no rebuild step).

Either way, AMRs are shown live over WebSocket as they move — nodes as
dots, edges as directed arrows, AMRs as a wheeled chassis (wheels spin
while moving; red when colliding with another AMR).

### Send an AMR somewhere

The viewer is read-only (no click-to-order UI). Issue moves via REST:

```bash
curl -X POST http://localhost:8000/api/orders \
  -H "Content-Type: application/json" \
  -d '{"amr_id": "amr-1", "target_node": "n3"}'
```

`amr_id` must be one of the ids listed in `config/sim_config.json`;
`target_node` must be a node id from the map. The simulator computes the
shortest path and drives the AMR there at `amr_speed` (meters/second).

Other useful endpoints:

```bash
curl http://localhost:8000/api/health   # liveness check
curl http://localhost:8000/api/map      # nodes/edges + amr footprint size
curl http://localhost:8000/api/amrs     # current position/heading/path/colliding per AMR
```

## Modifying the map

Maps live in `maps/*.json` as plain nodes + directed edges:

```json
{
  "nodes": [
    {"id": "n1", "x": 0.0, "y": 0.0},
    {"id": "n2", "x": 5.0, "y": 0.0}
  ],
  "edges": [
    {"from": "n1", "to": "n2"}
  ]
}
```

- `x`/`y` are meters on the factory floor.
- Edges are **directed**: `{"from": "n1", "to": "n2"}` only allows travel
  n1 → n2. List the reverse edge too if travel should work both ways.
- No curves, stations, or other metadata — just nodes and straight edges.

To use a different map, either edit `maps/sample_map.json` directly, or add
a new file under `maps/` and point `config/sim_config.json`'s `"map"` field
at it. Every `start_node` and `target_node` you use elsewhere in the config
must be a node id that exists in that map file.

## Adding more AMRs

Edit the `"amrs"` list in `config/sim_config.json`:

```json
{
  "amrs": [
    {"id": "amr-1", "start_node": "n1"},
    {"id": "amr-2", "start_node": "n3"},
    {"id": "amr-3", "start_node": "n2"}
  ]
}
```

- `id` must be unique within this config file.
- `start_node` must exist in the map referenced by `"map"`.
- All AMRs share the same speed (`amr_speed`) and footprint
  (`amr_width` x `amr_length`) — there's no per-AMR override, since this
  simulator only models one generic AMR type.
- Restart `main.py` after editing the config (it's loaded once at startup).

If you're running one instance per laptop (see below), give every AMR across
*all* laptops' configs a globally unique `id` — the beacon protocol
identifies AMRs by `id` alone, with no per-laptop namespacing.

## Starting the position beacon and watching its output

Every AMR broadcasts its position once per second over UDP (port `9999` by
default, `beacon_port`/`beacon_interval_s` in `config/sim_config.json`) —
this is what lets multiple laptops on the same LAN see each other's AMRs
without a central server. It starts automatically with `main.py`; there's
nothing separate to launch on the publishing side.

To watch the broadcasts, run the listener script (on the same laptop, a
different laptop on the same LAN, or several at once — it's just a passive
UDP listener, `beacon_client.py` doesn't run a simulator itself):

```bash
python beacon_client.py
```

It prints one line per beacon received, forever, until you Ctrl+C:

```text
Listening for AMR beacons on UDP port 9999...
{'amr_id': 'amr-1', 'x': 1.23, 'y': 0.0, 'goal': 'n3'}
{'amr_id': 'amr-2', 'x': 5.0, 'y': 5.0, 'goal': None}
```

`goal` is the AMR's final destination node, or `None` when it's idle.

**Multi-laptop setup**: run `main.py` on each of the 3 laptops (each with its
own `config/sim_config.json` listing that laptop's AMRs, all on the same
`beacon_port`), and run `beacon_client.py` anywhere on the LAN — it will
print beacons from all 3 laptops' AMRs, distinguished by `amr_id`.

> Note: this reimplements the beacon over plain UDP broadcast (Python's
> `socket` module) rather than the real ZeroMQ/CZMQ `zbeacon`/Zyre protocol,
> to avoid needing the `czmq` C library installed on every laptop. If your
> network blocks broadcast traffic (some corporate/guest Wi-Fi does), the
> publisher logs a one-time warning per AMR to stderr and keeps running —
> the REST API and viewer are unaffected either way.

## Running the tests

```bash
python -m pytest tests/ -v
```
