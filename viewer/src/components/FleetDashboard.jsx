import React, { useEffect, useRef, useState } from "react";
import { apiUrl } from "../api";

export function FleetDashboard({
  amrs,
  selectedNode,
  onSelectNode,
  theme = "light",
  onToggleTheme,
  onOpenMonitor,
  userSession,
  onAmrSpawned,
}) {
  const [activeTab, setActiveTab] = useState("activity"); // "activity" | "dispatch" | "cbba" | "network"
  const [langMode, setLangMode] = useState("human"); // "human" (NLP) | "machine" (UDP Code)
  const [mapNodes, setMapNodes] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [cbbaData, setCbbaData] = useState(null);
  const [pickupNode, setPickupNode] = useState("");
  const [dropoffNode, setDropoffNode] = useState("");
  const [priority, setPriority] = useState(1);
  const [loading, setLoading] = useState(false);
  const [statusMsg, setStatusMsg] = useState("");
  const [autoSimActive, setAutoSimActive] = useState(false);
  const [popupAlert, setPopupAlert] = useState(null); // { title, message, type: "error" | "success" }
  const [showSpawnModal, setShowSpawnModal] = useState(false);
  const [spawnNode, setSpawnNode] = useState("");
  const [spawnLoading, setSpawnLoading] = useState(false);
  const [editingAmrId, setEditingAmrId] = useState(null);
  const [editingName, setEditingName] = useState("");


  // Dynamic Map Editor State
  const [newNodeId, setNewNodeId] = useState("");
  const [newNodeX, setNewNodeX] = useState(15.0);
  const [newNodeY, setNewNodeY] = useState(5.0);
  const [newNodeType, setNewNodeType] = useState("dock");
  const [connectToNode, setConnectToNode] = useState("");
  const [edgeFrom, setEdgeFrom] = useState("");
  const [edgeTo, setEdgeTo] = useState("");
  const [logs, setLogs] = useState([
    {
      id: "init-1",
      timestamp: new Date().toLocaleTimeString(),
      type: "SYSTEM",
      machine: "MESH_BOOT::DECENTRALIZED_P2P_ONLINE(port=9999)",
      human: "Decentralized P2P Mesh online! Listening on UDP Port 9999.",
    },
  ]);

  const prevAmrStatesRef = useRef({});
  const prevTasksRef = useRef({});
  const prevP2PDialogueCountRef = useRef(0);

  // 1. Fetch map nodes
  useEffect(() => {
    fetch(apiUrl("/api/map"))
      .then((res) => res.json())
      .then((data) => {
        if (data.nodes) {
          setMapNodes(data.nodes);
          if (data.nodes.length >= 2) {
            setPickupNode(data.nodes[0].id);
            setDropoffNode(data.nodes[1].id);
          }
        }
      })
      .catch((err) => console.error("Failed to load map:", err));
  }, []);

  // 2. Poll tasks and CBBA state
  useEffect(() => {
    const pollServer = async () => {
      try {
        const [taskRes, cbbaRes] = await Promise.all([
          fetch(apiUrl("/api/tasks")),
          fetch(apiUrl("/api/cbba/state")),
        ]);
        if (taskRes.ok) {
          const taskList = await taskRes.json();
          setTasks(taskList);
          detectTaskEvents(taskList);
        }
        if (cbbaRes.ok) {
          const stateData = await cbbaRes.json();
          setCbbaData(stateData);

          // Detect new P2P Dialogues
          const dialogues = stateData?.network_telemetry?.p2p_dialogues || [];
          if (dialogues.length > prevP2PDialogueCountRef.current) {
            const newDialogues = dialogues.slice(prevP2PDialogueCountRef.current);
            newDialogues.forEach((d) => {
              addBilingualLog(
                "P2P",
                d.machine_protocol || `UDP::P2P_TRAFFIC(source=${d.source}, target=${d.target})`,
                d.human_speech || `[${d.source} ➔ ${d.target}] "Claiming right-of-way on corridor. Thank you for yielding."`
              );
            });
            prevP2PDialogueCountRef.current = dialogues.length;
          }
        }
      } catch (err) {
        // silent polling
      }
    };

    pollServer();
    const interval = setInterval(pollServer, 1000);
    return () => clearInterval(interval);
  }, []);

  // 3. Track state changes for real-time activity log
  useEffect(() => {
    amrs.forEach((amr) => {
      const prev = prevAmrStatesRef.current[amr.id];
      const state = amr.state_label || (amr.path.length > 0 ? "TRANSIT" : "IDLE");

      if (prev && prev.state !== state) {
        let logType = "NAV";
        if (state === "YIELDING") logType = "SAFETY";
        if (state === "FAILED") logType = "ALERT";
        if (state === "TRANSIT") logType = "NAV";

        const machineCode = `STATE_CHANGE(agent=${amr.id}, state=${state}, task=${amr.active_task || "NONE"})`;
        let humanSpeech = `${amr.id}: Switched to state [${state}].`;
        if (state === "TRANSIT") humanSpeech = `${amr.id}: En route to next station for ${amr.active_task || "order"}.`;
        if (state === "YIELDING") humanSpeech = `${amr.id}: Yielding corridor to peer. Pausing/detouring safely.`;
        if (state === "FAILED") humanSpeech = `⚠️ ${amr.id}: Hardware fault detected! Releasing tasks to mesh.`;

        addBilingualLog(logType, machineCode, humanSpeech);
      }

      prevAmrStatesRef.current[amr.id] = {
        state: state,
        isColliding: amr.colliding,
      };
    });
  }, [amrs]);

  const detectTaskEvents = (newTasks) => {
    newTasks.forEach((t) => {
      const prev = prevTasksRef.current[t.id];
      if (!prev) {
        addBilingualLog(
          "TASK",
          `TASK_INJECT(id=${t.id}, pickup=${t.pickup_node}, dropoff=${t.dropoff_node}, prio=${t.priority})`,
          `Dispatch: New Task [${t.id}] registered. Pickup at Station ${t.pickup_node} ➔ Deliver to Station ${t.dropoff_node} (Priority P${t.priority}).`
        );
      } else {
        if (prev.status !== t.status) {
          if (t.status === "COMPLETED") {
            addBilingualLog(
              "SUCCESS",
              `TASK_COMPLETED(id=${t.id}, agent=${t.assigned_to})`,
              `✅ ${t.assigned_to}: "Delivery completed for task [${t.id}]! Returning payload to Station ${t.dropoff_node}."`
            );
          } else if (t.status === "IN_PROGRESS" && prev.status === "UNASSIGNED") {
            addBilingualLog(
              "CBBA",
              `CONSENSUS_WON(id=${t.id}, winner=${t.assigned_to})`,
              `🤝 CBBA Consensus: ${t.assigned_to} won the auction for task [${t.id}] and accepted the mission.`
            );
          }
        }
      }
      prevTasksRef.current[t.id] = { ...t };
    });
  };

  const addBilingualLog = (type, machine, human) => {
    setLogs((prev) => [
      {
        id: `${Date.now()}-${Math.random()}`,
        timestamp: new Date().toLocaleTimeString(),
        type,
        machine,
        human,
      },
      ...prev.slice(0, 40),
    ]);
  };

  // 4. Dispatch single task
  const handleDispatch = async (e) => {
    if (e) e.preventDefault();
    if (!pickupNode || !dropoffNode) return;
    if (pickupNode === dropoffNode) {
      setStatusMsg("Pickup & Dropoff stations must be different");
      return;
    }

    setLoading(true);
    setStatusMsg("");

    try {
      const res = await fetch(apiUrl("/api/tasks"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pickup_node: pickupNode,
          dropoff_node: dropoffNode,
          priority: parseInt(priority, 10),
        }),
      });

      if (res.ok) {
        setStatusMsg("Task submitted to decentralized CBBA pool!");
        setPopupAlert({
          title: "🚀 Task Accepted by Fleet",
          message: `Task [Station ${pickupNode} ➔ Station ${dropoffNode}] submitted to decentralized CBBA auction pool!`,
          type: "success",
        });
        setTimeout(() => {
          setStatusMsg("");
          setPopupAlert(null);
        }, 4000);
      } else {
        const data = await res.json();
        const errorMsg = data.detail || "Dispatch failed";
        setStatusMsg(`Error: ${errorMsg}`);
        setPopupAlert({
          title: "🚨 Task Dispatch Blocked",
          message: errorMsg,
          type: "error",
        });
        addBilingualLog(
          "ALERT",
          `TASK_REJECTED::DOCK_BLOCKED(detail="${errorMsg}")`,
          `🚨 Dispatch Error: ${errorMsg}`
        );
      }
    } catch (err) {
      setStatusMsg("Network error");
      setPopupAlert({
        title: "🚨 Network Connection Error",
        message: "Failed to connect to fleet server. Ensure backend is running.",
        type: "error",
      });
    } finally {
      setLoading(false);
    }
  };

  // 5. Preset Route Dispatch
  const handlePresetDispatch = async (from, to, prio = 2) => {
    try {
      const res = await fetch(apiUrl("/api/tasks"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pickup_node: from,
          dropoff_node: to,
          priority: prio,
        }),
      });

      if (res.ok) {
        setStatusMsg(`Dispatched preset: [${from} ➔ ${to}]`);
        setPopupAlert({
          title: "🚀 Preset Route Dispatched",
          message: `Preset route [Station ${from} ➔ Station ${to}] dispatched to fleet!`,
          type: "success",
        });
        setTimeout(() => {
          setStatusMsg("");
          setPopupAlert(null);
        }, 3500);
      } else {
        const data = await res.json();
        const errorMsg = data.detail || "Preset route failed";
        setStatusMsg(`Error: ${errorMsg}`);
        setPopupAlert({
          title: "🚨 Preset Route Blocked",
          message: errorMsg,
          type: "error",
        });
        addBilingualLog(
          "ALERT",
          `PRESET_REJECTED(detail="${errorMsg}")`,
          `🚨 Preset Blocked: ${errorMsg}`
        );
      }
    } catch (err) {
      setStatusMsg("Network error");
    }
  };

  // 6. Auto-Traffic Simulator Loop
  useEffect(() => {
    let timer = null;
    if (autoSimActive && mapNodes.length >= 4) {
      timer = setInterval(() => {
        const fromIdx = Math.floor(Math.random() * mapNodes.length);
        let toIdx = Math.floor(Math.random() * mapNodes.length);
        while (toIdx === fromIdx) {
          toIdx = Math.floor(Math.random() * mapNodes.length);
        }
        const prio = Math.floor(Math.random() * 3) + 1;
        handlePresetDispatch(mapNodes[fromIdx].id, mapNodes[toIdx].id, prio);
      }, 4000);
    }
    return () => {
      if (timer) clearInterval(timer);
    };
  }, [autoSimActive, mapNodes]);

  // 7. Toggle Fault / Kill node
  const handleToggleKill = async (amrId, isAlive) => {
    const endpoint = isAlive ? `/api/nodes/${amrId}/kill` : `/api/nodes/${amrId}/recover`;
    await fetch(apiUrl(endpoint), { method: "POST" });
    addBilingualLog(
      isAlive ? "ALERT" : "SYSTEM",
      isAlive ? `FAULT_TRIGGER(agent=${amrId})` : `NODE_RECOVER(agent=${amrId})`,
      isAlive
        ? `🚨 Simulated fault on ${amrId}. Edge node stopped. Tasks released to surviving fleet.`
        : `🔧 ${amrId} restored to service and re-joined peer mesh.`
    );
  };

  // 8. Dispatch to Charging Pad
  const handleSendToCharge = async (amrId) => {
    const chargeNode = mapNodes.find((n) => n.type === "charging")?.id || (mapNodes.length > 0 ? mapNodes[mapNodes.length - 1].id : "n14");
    await fetch(apiUrl(`/api/nodes/${amrId}/charge`), { method: "POST" });
    addBilingualLog(
      "ALERT",
      `CHARGE_DISPATCH(agent=${amrId}, target=${chargeNode})`,
      `⚡ Battery critical protocol initiated for ${amrId}! Autonomous return to Charging Bay Station ${chargeNode}.`
    );
  };

  // 9. Spawn Additional AMR (Max 3 per User)
  const handleSpawnAmr = async () => {
    const targetNode = spawnNode || (mapNodes[0]?.id || "n1");
    setSpawnLoading(true);
    try {
      const res = await fetch(apiUrl("/api/amrs/spawn"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: userSession?.email || "guest",
          start_node: targetNode,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setPopupAlert({
          title: "🤖 AMR Deployed",
          message: `Successfully spawned ${data.amr_id} at Station ${data.start_node}!`,
          type: "success",
        });
        if (onAmrSpawned) {
          onAmrSpawned(data.amr_id, data.amrs);
        }
        setShowSpawnModal(false);
      } else {
        setPopupAlert({
          title: "Spawn Limit",
          message: data.detail || "Could not spawn AMR.",
          type: "error",
        });
      }
    } catch (err) {
      setPopupAlert({
        title: "Error",
        message: "Network error spawning AMR.",
        type: "error",
      });
    } finally {
      setSpawnLoading(false);
    }
  };

  // 10. Rename AMR and Persist to Supabase Database
  const handleRenameAmr = async (amrId) => {
    const trimmed = editingName.trim();
    if (!trimmed) {
      setEditingAmrId(null);
      return;
    }
    try {
      const res = await fetch(apiUrl(`/api/amrs/${amrId}/rename`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmed }),
      });
      if (res.ok) {
        setEditingAmrId(null);
        setEditingName("");
        setPopupAlert({
          title: "AMR Renamed",
          message: `AMR '${amrId}' display name updated to '${trimmed}' and saved to Supabase database.`,
          type: "success",
        });
        addBilingualLog(
          "SUCCESS",
          `AMR_RENAME(id=${amrId}, name="${trimmed}")`,
          `✏️ AMR [${amrId}] renamed to "${trimmed}" (Saved to Supabase).`
        );
      } else {
        const err = await res.json();
        setPopupAlert({ title: "Error", message: err.detail || "Failed to rename AMR.", type: "error" });
      }
    } catch (err) {
      setPopupAlert({ title: "Error", message: "Network error renaming AMR.", type: "error" });
    }
  };


  // 10. Decommission / Remove AMR
  const handleRemoveAmr = async (amrId) => {
    try {
      const res = await fetch(apiUrl(`/api/amrs/${amrId}`), {
        method: "DELETE",
      });
      const data = await res.json();
      if (res.ok) {
        setPopupAlert({
          title: "🤖 AMR Decommissioned",
          message: `Successfully removed ${amrId} and freed its station.`,
          type: "success",
        });
        if (onAmrRemoved) {
          onAmrRemoved(amrId);
        }
      } else {
        setPopupAlert({
          title: "Error",
          message: data.detail || `Could not remove ${amrId}.`,
          type: "error",
        });
      }
    } catch (err) {
      setPopupAlert({
        title: "Network Error",
        message: "Failed to connect to backend server.",
        type: "error",
      });
    }
  };

  // 11. Dynamic Map Editor Handlers
  const handleAddStation = async (e) => {
    e.preventDefault();
    if (!newNodeId.trim()) return;
    try {
      const res = await fetch(apiUrl("/api/map/nodes"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: newNodeId.trim(),
          x: parseFloat(newNodeX) || 0,
          y: parseFloat(newNodeY) || 0,
          type: newNodeType,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        if (connectToNode) {
          await fetch(apiUrl("/api/map/edges"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              from_node: newNodeId.trim(),
              to_node: connectToNode,
              bidirectional: true,
            }),
          });
        }
        setPopupAlert({
          title: "📍 Station Added",
          message: `Station ${newNodeId} dynamically added to warehouse layout!`,
          type: "success",
        });
        setNewNodeId("");
        fetch(apiUrl("/api/map")).then(r => r.json()).then(d => { if (d.nodes) setMapNodes(d.nodes); });
      } else {
        setPopupAlert({ title: "Error", message: data.detail || "Failed to add station.", type: "error" });
      }
    } catch (err) {
      setPopupAlert({ title: "Error", message: "Failed to connect to backend.", type: "error" });
    }
  };

  const handleAddCorridor = async (e) => {
    e.preventDefault();
    if (!edgeFrom || !edgeTo || edgeFrom === edgeTo) return;
    try {
      const res = await fetch(apiUrl("/api/map/edges"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ from_node: edgeFrom, to_node: edgeTo, bidirectional: true }),
      });
      const data = await res.json();
      if (res.ok) {
        setPopupAlert({
          title: "🛣️ Corridor Connected",
          message: `Corridor connected between ${edgeFrom} and ${edgeTo}!`,
          type: "success",
        });
        fetch(apiUrl("/api/map")).then(r => r.json()).then(d => { if (d.nodes) setMapNodes(d.nodes); });
      } else {
        setPopupAlert({ title: "Error", message: data.detail || "Failed to connect corridor.", type: "error" });
      }
    } catch (err) {
      setPopupAlert({ title: "Error", message: "Failed to connect corridor.", type: "error" });
    }
  };

  const handleDeleteStation = async (nodeId) => {
    try {
      const res = await fetch(apiUrl(`/api/map/nodes/${nodeId}`), { method: "DELETE" });
      const data = await res.json();
      if (res.ok) {
        setPopupAlert({
          title: "📍 Station Removed",
          message: `Station ${nodeId} removed from map.`,
          type: "success",
        });
        fetch(apiUrl("/api/map")).then(r => r.json()).then(d => { if (d.nodes) setMapNodes(d.nodes); });
      } else {
        setPopupAlert({ title: "Error", message: data.detail || `Cannot delete station ${nodeId}.`, type: "error" });
      }
    } catch (err) {
      setPopupAlert({ title: "Error", message: "Failed to delete station.", type: "error" });
    }
  };

  const handleShuffleMap = async () => {
    try {
      const res = await fetch(apiUrl("/api/map/shuffle"), { method: "POST" });
      const data = await res.json();
      if (res.ok) {
        setPopupAlert({
          title: "🎲 Map Shuffled",
          message: data.message || "Warehouse layout regenerated with AMRs positioned safely!",
          type: "success",
        });
        fetch(apiUrl("/api/map")).then(r => r.json()).then(d => { if (d.nodes) setMapNodes(d.nodes); });
      } else {
        setPopupAlert({ title: "Error", message: data.detail || "Failed to shuffle map.", type: "error" });
      }
    } catch (err) {
      setPopupAlert({ title: "Error", message: "Network error shuffling map.", type: "error" });
    }
  };

  const getBadgeStyle = (state) => {
    switch (state) {
      case "TRANSIT":
        return "bg-sky-950 text-sky-300 border-sky-800";
      case "YIELDING":
        return "bg-amber-950 text-amber-300 border-amber-800";
      case "FAILED":
        return "bg-rose-950 text-rose-300 border-rose-800";
      default:
        return "bg-slate-800 text-slate-300 border-slate-700";
    }
  };

  const getLogTagStyle = (type) => {
    switch (type) {
      case "SUCCESS":
        return "bg-emerald-950 text-emerald-300 border-emerald-800";
      case "CBBA":
        return "bg-sky-950 text-sky-300 border-sky-800";
      case "P2P":
        return "bg-indigo-950 text-indigo-300 border-indigo-800 font-bold";
      case "ALERT":
        return "bg-rose-950 text-rose-300 border-rose-800";
      case "SAFETY":
        return "bg-amber-950 text-amber-300 border-amber-800";
      default:
        return "bg-slate-800 text-slate-400 border-slate-700";
    }
  };

  return (
    <div className="w-full h-full flex flex-col bg-slate-900 text-slate-100 select-none relative">
      {/* Floating Pop-Up Alert Modal / Toast */}
      {popupAlert && (
        <div className="absolute top-16 left-3 right-3 z-50 animate-in fade-in slide-in-from-top-3 duration-200">
          <div
            className={`p-3.5 rounded-xl border backdrop-blur-md shadow-2xl flex items-start justify-between gap-3 ${popupAlert.type === "error"
                ? "bg-rose-950/95 border-rose-500/80 text-rose-100 shadow-rose-950/80"
                : "bg-emerald-950/95 border-emerald-500/80 text-emerald-100 shadow-emerald-950/80"
              }`}
          >
            <div className="flex gap-2.5 items-start">
              <span className="text-xl leading-none mt-0.5">
                {popupAlert.type === "error" ? "🚨" : "✅"}
              </span>
              <div className="flex flex-col gap-0.5">
                <div className="font-bold text-xs tracking-wide">
                  {popupAlert.title}
                </div>
                <div className="text-[11px] text-slate-200 leading-relaxed font-sans">
                  {popupAlert.message}
                </div>
              </div>
            </div>
            <button
              onClick={() => setPopupAlert(null)}
              className="text-slate-400 hover:text-white p-1 rounded-md transition-colors text-xs font-bold"
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="px-4 py-3 border-b border-slate-800 bg-slate-950/90 flex justify-between items-center">
        <div className="flex items-center gap-2 font-bold text-sm tracking-wide text-slate-200">
          <span>🏭</span>
          <span>AMR FLEET CONTROL</span>
        </div>
        <div className="flex items-center gap-1.5">
          {onToggleTheme && (
            <button
              onClick={onToggleTheme}
              className="text-[10px] font-semibold px-2 py-1 rounded border border-slate-700 bg-slate-800 text-slate-300 hover:text-white transition-colors"
              title="Toggle Light / Dark Map Theme"
            >
              {theme === "light" ? "🌙 Dark Map" : "☀️ Light Map"}
            </button>
          )}
          <button
            onClick={() => onOpenMonitor ? onOpenMonitor() : window.open("/monitor", "_blank", "width=1280,height=820")}
            className="text-[10px] font-semibold px-2 py-1 rounded border border-cyan-700 bg-cyan-950 text-cyan-300 hover:bg-cyan-900 transition-colors flex items-center gap-1"
            title="Open Dedicated Fullscreen Telemetry & P2P Monitor in new window"
          >
            <span>↗ Monitor</span>
          </button>
          <button
            onClick={() => setLangMode(langMode === "human" ? "machine" : "human")}
            className="text-[10px] font-semibold px-2 py-1 rounded border border-slate-700 bg-slate-800 text-slate-300 hover:text-white transition-colors"
            title="Toggle between Natural Human Speech and Raw Machine UDP Code"
          >
            {langMode === "human" ? "🗣️ NLP" : "💻 Code"}
          </button>
          <button
            onClick={() => setAutoSimActive(!autoSimActive)}
            className={`text-[10px] font-semibold px-2 py-1 rounded border transition-colors ${autoSimActive
                ? "bg-emerald-950 border-emerald-700 text-emerald-300 animate-pulse"
                : "bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200"
              }`}
            title="Automatically generates warehouse traffic continuously"
          >
            {autoSimActive ? "● Auto ON" : "○ Auto OFF"}
          </button>
        </div>
      </div>

      {/* Interactive Map Node Quick Action Banner */}
      {selectedNode && (
        <div className="mx-3 mt-2.5 p-2 rounded-lg bg-pink-950/60 border border-pink-700 text-pink-200 flex items-center justify-between text-xs animate-in fade-in shadow-lg">
          <div className="flex items-center gap-1.5 font-bold">
            <span>📍 Dock [{selectedNode}] Selected</span>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => {
                setPickupNode(selectedNode);
                setActiveTab("dispatch");
              }}
              className="px-2 py-0.5 rounded bg-pink-800 hover:bg-pink-700 text-[10px] font-bold text-white transition"
            >
              Set Pickup
            </button>
            <button
              onClick={() => {
                setDropoffNode(selectedNode);
                setActiveTab("dispatch");
              }}
              className="px-2 py-0.5 rounded bg-pink-800 hover:bg-pink-700 text-[10px] font-bold text-white transition"
            >
              Set Dropoff
            </button>
            <button
              onClick={() => onSelectNode && onSelectNode(null)}
              className="px-1 text-slate-400 hover:text-white text-xs font-bold"
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {/* Tab Navigation */}
      <div className="flex border-b border-slate-800 bg-slate-950/40 text-xs font-semibold text-slate-400">
        <button
          onClick={() => setActiveTab("activity")}
          className={`flex-1 py-2 px-2 text-center border-b-2 transition-colors ${activeTab === "activity"
              ? "border-sky-500 text-sky-400 bg-slate-900/50"
              : "border-transparent hover:text-slate-200 hover:bg-slate-900/30"
            }`}
        >
          Activity ({logs.length})
        </button>
        <button
          onClick={() => setActiveTab("dispatch")}
          className={`flex-1 py-2 px-2 text-center border-b-2 transition-colors ${activeTab === "dispatch"
              ? "border-sky-500 text-sky-400 bg-slate-900/50"
              : "border-transparent hover:text-slate-200 hover:bg-slate-900/30"
            }`}
        >
          Dispatch
        </button>
        <button
          onClick={() => setActiveTab("cbba")}
          className={`flex-1 py-2 px-1 text-center border-b-2 transition-colors ${activeTab === "cbba"
              ? "border-sky-500 text-sky-400 bg-slate-900/50"
              : "border-transparent hover:text-slate-200 hover:bg-slate-900/30"
            }`}
        >
          CBBA
        </button>
        <button
          onClick={() => setActiveTab("map")}
          className={`flex-1 py-2 px-1 text-center border-b-2 transition-colors ${activeTab === "map"
              ? "border-emerald-500 text-emerald-400 bg-slate-900/50"
              : "border-transparent hover:text-slate-200 hover:bg-slate-900/30"
            }`}
        >
          Map
        </button>
        <button
          onClick={() => setActiveTab("network")}
          className={`flex-1 py-2 px-1 text-center border-b-2 transition-colors ${activeTab === "network"
              ? "border-sky-500 text-sky-400 bg-slate-900/50"
              : "border-transparent hover:text-slate-200 hover:bg-slate-900/30"
            }`}
        >
          P2P Mesh
        </button>
      </div>

      {/* Scrollable Body */}
      <div className="p-4 overflow-y-auto flex-1 flex flex-col gap-4 text-xs">
        {/* Section: Live Fleet Status Overview (Always Visible) */}
        <div>
          <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-2 flex justify-between items-center">
            <span>Active AMRs ({amrs.length})</span>
            <button
              onClick={() => {
                const isOpening = !showSpawnModal;
                if (isOpening) {
                  const vacant = mapNodes.find((n) => !amrs.some((a) => a.current_node === n.id));
                  if (vacant) setSpawnNode(vacant.id);
                }
                setShowSpawnModal(isOpening);
              }}
              className="px-2 py-0.5 rounded bg-indigo-600 hover:bg-indigo-500 text-white text-[10px] font-bold tracking-wide border border-indigo-400 shadow transition flex items-center gap-1"
              title="Spawn up to 6 AMRs under your account"
            >
              <span>+ Spawn AMR</span>
              <span className="opacity-85">({amrs.length}/6)</span>
            </button>
          </div>

          {/* Spawn AMR Station Selector Dropdown */}
          {showSpawnModal && (
            <div className="bg-slate-950 border border-indigo-500/60 rounded-xl p-2.5 mb-3 flex flex-col gap-2 animate-in fade-in duration-200 shadow-xl">
              <div className="flex justify-between items-center text-[11px]">
                <span className="font-bold text-indigo-300">Deploy Additional AMR</span>
                <span className="text-[10px] text-slate-400">Max 6 per User (Vacant Station Only)</span>
              </div>
              <div className="flex items-center gap-2">
                <select
                  value={spawnNode}
                  onChange={(e) => setSpawnNode(e.target.value)}
                  className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                >
                  {mapNodes.map((n) => {
                    const isOcc = amrs.some((a) => a.current_node === n.id);
                    return (
                      <option key={n.id} value={n.id} disabled={isOcc}>
                        Station {n.id} ({n.type || "Dock"}) {isOcc ? "⛔ [Occupied]" : "✅ [Vacant]"}
                      </option>
                    );
                  })}
                </select>
                <button
                  onClick={handleSpawnAmr}
                  disabled={spawnLoading}
                  className="bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold px-3 py-1.5 rounded-lg border border-emerald-400 shadow transition disabled:opacity-50"
                >
                  {spawnLoading ? "Deploying…" : "Spawn"}
                </button>
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-2">
            {amrs.map((amr) => {
              const state = amr.state_label || (amr.path.length > 0 ? "TRANSIT" : "IDLE");
              const isAlive = state !== "FAILED";
              const battery = amr.battery_soc !== undefined ? amr.battery_soc : 100;

              return (
                <div
                  key={amr.id}
                  className={`bg-slate-950/80 border rounded-lg p-2.5 flex flex-col gap-1.5 transition-colors ${!isAlive ? "border-rose-900 bg-rose-950/20" : "border-slate-800"
                    }`}
                >
                  <div className="flex justify-between items-center gap-1">
                    {editingAmrId === amr.id ? (
                      <div className="flex items-center gap-1 flex-1 mr-1">
                        <input
                          type="text"
                          value={editingName}
                          onChange={(e) => setEditingName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleRenameAmr(amr.id);
                            if (e.key === "Escape") setEditingAmrId(null);
                          }}
                          autoFocus
                          placeholder="New name..."
                          className="w-full bg-slate-900 border border-indigo-500 rounded px-1.5 py-0.5 text-[11px] text-white focus:outline-none"
                        />
                        <button
                          onClick={() => handleRenameAmr(amr.id)}
                          className="text-[10px] bg-emerald-600 hover:bg-emerald-500 text-white px-1.5 py-0.5 rounded font-bold transition shadow"
                          title="Save to Database"
                        >
                          ✓
                        </button>
                        <button
                          onClick={() => setEditingAmrId(null)}
                          className="text-[10px] bg-slate-700 hover:bg-slate-600 text-slate-300 px-1 py-0.5 rounded font-bold transition"
                          title="Cancel"
                        >
                          ✕
                        </button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-1 truncate flex-1 mr-1">
                        <span className="font-bold text-slate-100 truncate" title={amr.id}>
                          {amr.name || amr.id}
                        </span>
                        <button
                          onClick={() => {
                            setEditingAmrId(amr.id);
                            setEditingName(amr.name || amr.id);
                          }}
                          className="text-[10px] text-slate-500 hover:text-indigo-400 transition"
                          title="Rename AMR (Saves to Supabase Database)"
                        >
                          ✏️
                        </button>
                      </div>
                    )}
                    <span
                      className={`text-[10px] font-semibold px-2 py-0.5 rounded border ${getBadgeStyle(
                        state
                      )}`}
                    >
                      {state}
                    </span>
                  </div>


                  <div className="flex justify-between text-slate-400 text-[11px]">
                    <span>Active Task:</span>
                    <span
                      className="text-slate-200 font-medium truncate max-w-[105px]"
                      title={
                        amr.active_task
                          ? amr.has_payload || amr.subtask === "DROPOFF"
                            ? `${amr.active_task} (Loaded 📦 Delivering)`
                            : `${amr.active_task} (Empty ➔ Pickup Dock)`
                          : "Idle"
                      }
                    >
                      {amr.active_task
                        ? amr.has_payload || amr.subtask === "DROPOFF"
                          ? `${amr.active_task} 📦`
                          : `${amr.active_task} (Pickup)`
                        : "Idle"}
                    </span>
                  </div>

                  {/* Live Waypoints Route Path */}
                  <div className="flex justify-between items-center text-[10px] bg-slate-900/60 rounded px-1.5 py-0.5 border border-slate-800/80">
                    <span className="text-slate-500 font-medium">Path:</span>
                    <span
                      className="font-mono text-emerald-400 font-bold truncate max-w-[125px]"
                      title={
                        amr.path && amr.path.length > 0
                          ? [amr.current_node, ...amr.path].join(" ➔ ")
                          : `Parked at ${amr.current_node}`
                      }
                    >
                      {amr.path && amr.path.length > 0
                        ? [amr.current_node, ...amr.path].join(" ➔ ")
                        : `Parked at ${amr.current_node || "Dock"}`}
                    </span>
                  </div>

                  <div className="flex justify-between text-slate-400 text-[11px]">
                    <span>Battery:</span>
                    <span className="text-slate-200 font-medium">
                      {typeof battery === "number" ? battery.toFixed(1) : battery}%
                    </span>
                  </div>

                  <div className="w-full h-1 bg-slate-800 rounded-full overflow-hidden">
                    <div
                      className="h-full transition-all duration-300"
                      style={{
                        width: `${battery}%`,
                        backgroundColor:
                          battery > 50 ? "#10b981" : battery > 20 ? "#f59e0b" : "#ef4444",
                      }}
                    />
                  </div>

                  <div className="flex justify-between items-center pt-1 gap-1 flex-wrap">
                    <button
                      className="text-[10px] px-2 py-0.5 rounded border border-amber-800/80 bg-amber-950/40 hover:bg-amber-900/60 text-amber-300 transition-colors flex items-center gap-0.5"
                      onClick={() => handleSendToCharge(amr.id)}
                      title="Dispatch to Charging Pad"
                    >
                      ⚡ Charge
                    </button>
                    <button
                      className="text-[10px] px-2 py-0.5 rounded border border-slate-700 bg-slate-800/80 hover:bg-slate-700 text-slate-300 transition-colors"
                      onClick={() => handleToggleKill(amr.id, isAlive)}
                    >
                      {isAlive ? "Sim Fault" : "Recover"}
                    </button>
                    <button
                      className="text-[10px] px-1.5 py-0.5 rounded border border-rose-900/60 bg-rose-950/30 hover:bg-rose-900/60 text-rose-300 transition-colors"
                      onClick={() => handleRemoveAmr(amr.id)}
                      title="Decommission and remove this AMR from fleet"
                    >
                      ✕ Despawn
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* TAB 1: Real-Time Bilingual Activity Log (NLP + Code) */}
        {activeTab === "activity" && (
          <div className="flex flex-col gap-2 flex-1">
            <div className="flex justify-between items-center text-[11px] font-bold uppercase tracking-wider text-slate-400">
              <div className="flex items-center gap-2">
                <span>{langMode === "human" ? "🗣️ Natural Fleet Dialogue" : "💻 Raw Machine Telemetry"}</span>
              </div>
              <button
                onClick={() => setLogs([])}
                className="text-[10px] text-slate-500 hover:text-slate-300 font-normal"
              >
                Clear
              </button>
            </div>

            <div className="h-56 overflow-y-auto bg-slate-950/80 border border-slate-800 rounded-lg p-2.5 flex flex-col gap-1.5 text-[11px]">
              {logs.length === 0 ? (
                <div className="text-slate-600 text-center py-12 font-sans">
                  No recent activity. Dispatch a task to see live bilingual speech.
                </div>
              ) : (
                logs.map((log) => (
                  <div key={log.id} className="flex items-start gap-1.5 leading-relaxed border-b border-slate-900/60 pb-1">
                    <span className="text-slate-500 text-[10px] whitespace-nowrap font-mono">
                      {log.timestamp}
                    </span>
                    <span
                      className={`text-[9px] px-1 py-0.2 rounded border font-sans font-semibold uppercase whitespace-nowrap ${getLogTagStyle(
                        log.type
                      )}`}
                    >
                      {log.type}
                    </span>
                    <span className={langMode === "human" ? "text-slate-200 flex-1 font-sans" : "text-emerald-400 flex-1 font-mono text-[10px]"}>
                      {langMode === "human" ? log.human : log.machine}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        )}

        {/* TAB 2: Task Dispatch & Route Presets */}
        {activeTab === "dispatch" && (
          <div className="flex flex-col gap-3">
            <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
              Custom Warehouse Task
            </div>

            <form
              onSubmit={handleDispatch}
              className="bg-slate-950/80 border border-slate-800 rounded-lg p-3 flex flex-col gap-2.5"
            >
              <div className="grid grid-cols-5 gap-2">
                <div className="col-span-2 flex flex-col gap-1">
                  <label className="text-[10px] text-slate-400 font-medium">Pickup Station</label>
                  <select
                    className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-slate-200 text-xs focus:outline-none focus:border-sky-500"
                    value={pickupNode}
                    onChange={(e) => setPickupNode(e.target.value)}
                  >
                    {mapNodes.map((n) => (
                      <option key={n.id} value={n.id}>
                        Station {n.id}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="col-span-2 flex flex-col gap-1">
                  <label className="text-[10px] text-slate-400 font-medium">Dropoff Station</label>
                  <select
                    className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-slate-200 text-xs focus:outline-none focus:border-sky-500"
                    value={dropoffNode}
                    onChange={(e) => setDropoffNode(e.target.value)}
                  >
                    {mapNodes.map((n) => (
                      <option key={n.id} value={n.id}>
                        Station {n.id}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="col-span-1 flex flex-col gap-1">
                  <label className="text-[10px] text-slate-400 font-medium">Priority</label>
                  <select
                    className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-slate-200 text-xs focus:outline-none focus:border-sky-500"
                    value={priority}
                    onChange={(e) => setPriority(e.target.value)}
                  >
                    <option value={1}>1 (Low)</option>
                    <option value={2}>2 (Med)</option>
                    <option value={3}>3 (High)</option>
                  </select>
                </div>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full bg-sky-700 hover:bg-sky-600 active:bg-sky-800 text-white font-semibold py-2 px-3 rounded text-xs transition-colors shadow"
              >
                {loading ? "Allocating via CBBA..." : "🚀 Dispatch Task to Fleet"}
              </button>
            </form>

            {/* Quick Presets */}
            <div>
              <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-2">
                Quick Route Presets
              </div>
              <div className="grid grid-cols-2 gap-2">
                <button
                  onClick={() => handlePresetDispatch("n1", "n4", 3)}
                  className="p-2 bg-slate-950/80 hover:bg-slate-800 border border-slate-800 rounded-lg text-left text-slate-300 text-[11px] transition-colors"
                >
                  <div className="font-semibold text-slate-200">Inbound ➔ Outbound</div>
                  <div className="text-slate-500 text-[10px]">n1 ➔ n4 (High Priority)</div>
                </button>

                <button
                  onClick={() => handlePresetDispatch("n3", "n10", 2)}
                  className="p-2 bg-slate-950/80 hover:bg-slate-800 border border-slate-800 rounded-lg text-left text-slate-300 text-[11px] transition-colors"
                >
                  <div className="font-semibold text-slate-200">Storage A ➔ Dock</div>
                  <div className="text-slate-500 text-[10px]">n3 ➔ n10 (Med Priority)</div>
                </button>
              </div>
            </div>

            {/* Active & Recent Task Pool */}
            <div>
              <div className="flex justify-between items-center text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-2">
                <span>Active & Recent Tasks ({tasks.length})</span>
                <div className="flex gap-1.5">
                  <button
                    type="button"
                    onClick={async () => {
                      await fetch(apiUrl("/api/tasks/clear"), { method: "POST" });
                      const r = await fetch(apiUrl("/api/tasks"));
                      if (r.ok) setTasks(await r.json());
                    }}
                    className="text-[10px] text-slate-400 hover:text-slate-200 border border-slate-800 px-1.5 py-0.5 rounded bg-slate-900"
                    title="Clear completed tasks from pool"
                  >
                    🧹 Clear Done
                  </button>
                  <button
                    type="button"
                    onClick={async () => {
                      await fetch(apiUrl("/api/tasks/clear?include_active=true"), { method: "POST" });
                      const r = await fetch(apiUrl("/api/tasks"));
                      if (r.ok) setTasks(await r.json());
                    }}
                    className="text-[10px] text-rose-400 hover:text-rose-200 border border-rose-900/60 px-1.5 py-0.5 rounded bg-rose-950/40"
                    title="Stop and reset all tasks"
                  >
                    🛑 Reset
                  </button>
                </div>
              </div>

              <div className="max-h-36 overflow-y-auto bg-slate-950/80 border border-slate-800 rounded-lg p-1.5 flex flex-col gap-1">
                {tasks.length === 0 ? (
                  <div className="text-slate-500 text-center py-4 text-[10px] italic">
                    No active tasks. AMRs will remain safely idle at stations.
                  </div>
                ) : (
                  tasks.map((t) => (
                    <div
                      key={t.id}
                      className="p-1.5 bg-slate-900/70 border border-slate-800 rounded flex items-center justify-between text-[10px]"
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono font-bold text-slate-200">{t.id}</span>
                        <span className="text-slate-400">
                          {t.pickup_node} ➔ {t.dropoff_node}
                        </span>
                        <span className="text-slate-500">(P{t.priority})</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span
                          className={`px-1.5 py-0.2 rounded font-semibold text-[9px] ${
                            t.status === "COMPLETED"
                              ? "bg-emerald-950 text-emerald-300 border border-emerald-800"
                              : t.status === "IN_PROGRESS"
                              ? "bg-cyan-950 text-cyan-300 border border-cyan-800"
                              : "bg-amber-950 text-amber-300 border border-amber-800"
                          }`}
                        >
                          {t.status}
                        </span>
                        {t.assigned_to && (
                          <span className="text-indigo-300 font-semibold">{t.assigned_to}</span>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        )}

        {/* TAB 3: CBBA Comparative Bidding Matrix Table with Planned Waypoints */}
        {activeTab === "cbba" && (
          <div className="flex flex-col gap-2">
            <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
              Live CBBA Bidding, Winner & Route Path
            </div>

            <div className="max-h-56 overflow-y-auto bg-slate-950/80 border border-slate-800 rounded-lg">
              {tasks.length === 0 ? (
                <div className="p-4 text-slate-500 text-center text-xs">
                  No tasks in pool. Dispatch a task to see bidding.
                </div>
              ) : (
                <table className="w-full text-left text-[11px] border-collapse font-mono">
                  <thead className="bg-slate-900/90 text-slate-400 sticky top-0 font-sans">
                    <tr>
                      <th className="py-1.5 px-2 font-semibold">Task</th>
                      {(amrs && amrs.length > 0 ? amrs : [{ id: "amr-1" }, { id: "amr-2" }]).map((a) => (
                        <th key={a.id} className="py-1.5 px-1 text-center font-semibold text-cyan-300">
                          {a.id}
                        </th>
                      ))}
                      <th className="py-1.5 px-1.5 font-semibold">Winner</th>
                      <th className="py-1.5 px-2 font-semibold">Travel Path</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {(cbbaData?.bid_matrix || []).slice(-8).reverse().map((row) => {
                      const activeAmrList = amrs && amrs.length > 0 ? amrs : [{ id: "amr-1" }, { id: "amr-2" }];
                      return (
                        <tr key={row.task_id} className="hover:bg-slate-900/40">
                          <td className="py-1.5 px-2 font-mono font-medium text-slate-300">
                            {row.task_id}
                            <div className="text-[9px] text-slate-500">
                              {row.pickup || row.pickup_node}➔{row.dropoff || row.dropoff_node}
                            </div>
                          </td>
                          {activeAmrList.map((a) => {
                            const aid = a.id;
                            const shortId = aid.replace(/^[a-z]+-/, "");
                            const bid = row.bids?.[aid] ?? row.bids?.[shortId] ?? 0.0;
                            const isWinner =
                              row.assigned_to === aid ||
                              row.assigned_to === shortId ||
                              row.winner === aid ||
                              row.winner === shortId;
                            return (
                              <td
                                key={aid}
                                className={`py-1.5 px-1 text-center ${isWinner
                                    ? "text-emerald-400 font-bold bg-emerald-950/40 border border-emerald-800/50 rounded"
                                    : "text-slate-400"
                                  }`}
                              >
                                {bid > 0 ? bid.toFixed(1) : "—"}
                              </td>
                            );
                          })}
                          <td className="py-1.5 px-1.5 font-semibold text-sky-400">
                            {row.assigned_to || row.winner || "Bidding"}
                          </td>
                          <td className="py-1.5 px-2 font-mono text-[10px] text-emerald-400 font-semibold whitespace-nowrap">
                            {row.planned_route
                              ? row.planned_route.join(" ➔ ")
                              : `${row.pickup || row.pickup_node} ➔ ${row.dropoff || row.dropoff_node}`}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        )}

        {/* TAB 4: P2P Mesh Network Telemetry & Live Dialogue Inspector */}
        {activeTab === "network" && (
          <div className="flex flex-col gap-3">
            <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
              Mesh Network & P2P Gossip Status
            </div>

            <div className="bg-slate-950/80 border border-slate-800 rounded-lg p-3 flex flex-col gap-2">
              <div className="flex justify-between">
                <span className="text-slate-400">Protocol:</span>
                <span className="font-mono text-slate-200">UDP Mesh (Port 9999)</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Active Nodes:</span>
                <span className="font-mono text-emerald-400">
                  {cbbaData?.network_telemetry?.active_nodes_count || 4} / 4 Online
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">P2P Packets Exchanged:</span>
                <span className="font-mono text-slate-200">
                  {cbbaData?.network_telemetry?.total_packets_exchanged || 0}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Average P2P Latency:</span>
                <span className="font-mono text-emerald-400">1.2 ms (Local WiFi)</span>
              </div>
            </div>

            <div>
              <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-1.5">
                Bilingual P2P Dialogue Negotiations
              </div>
              <div className="h-32 overflow-y-auto bg-slate-950/80 border border-slate-800 rounded-lg p-2 text-[10px] flex flex-col gap-1.5">
                {(cbbaData?.network_telemetry?.p2p_dialogues || []).length === 0 ? (
                  <div className="text-slate-600 text-center py-4 font-sans">
                    No active corridor conflicts. Nodes listening on mesh.
                  </div>
                ) : (
                  cbbaData.network_telemetry.p2p_dialogues.map((d, idx) => (
                    <div key={idx} className="bg-slate-900/60 border border-slate-800/80 rounded p-1.5 leading-tight">
                      <div className="flex justify-between text-indigo-300 font-bold font-mono">
                        <span>💬 {d.source} ⇄ {d.target}</span>
                        <span className="text-slate-500">{d.time}</span>
                      </div>
                      <div className="text-slate-200 text-[10px] mt-1 italic font-sans">
                        "{d.human_speech || d.message}"
                      </div>
                      <div className="text-emerald-400 font-mono text-[9px] mt-0.5">
                        {d.machine_protocol || `UDP::ACK(win=${d.winner})`}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        )}

        {/* TAB 5: Dynamic Warehouse Map & Station Builder */}
        {activeTab === "map" && (
          <div className="flex flex-col gap-3">
            <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
              Dynamic Warehouse Station & Corridor Builder
            </div>

            {/* Form: Add New Station Node */}
            <form
              onSubmit={handleAddStation}
              className="bg-slate-950/80 border border-slate-800 rounded-lg p-3 flex flex-col gap-2.5 shadow-inner"
            >
              <div className="font-bold text-slate-200 text-xs flex items-center gap-1.5">
                <span>📍 Add New Station Node</span>
              </div>

              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="text-[10px] text-slate-400 font-semibold">Station ID</label>
                  <input
                    type="text"
                    value={newNodeId}
                    onChange={(e) => setNewNodeId(e.target.value)}
                    placeholder="e.g. n15"
                    required
                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-emerald-500"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-slate-400 font-semibold">Coord X (m)</label>
                  <input
                    type="number"
                    step="0.5"
                    value={newNodeX}
                    onChange={(e) => setNewNodeX(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-emerald-500"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-slate-400 font-semibold">Coord Y (m)</label>
                  <input
                    type="number"
                    step="0.5"
                    value={newNodeY}
                    onChange={(e) => setNewNodeY(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-emerald-500"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[10px] text-slate-400 font-semibold">Station Type</label>
                  <select
                    value={newNodeType}
                    onChange={(e) => setNewNodeType(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100 text-xs focus:outline-none focus:ring-1 focus:ring-emerald-500"
                  >
                    <option value="dock">Dock (Pickup/Dropoff)</option>
                    <option value="charging">⚡ Charging Bay</option>
                    <option value="aisle">Aisle Waypoint</option>
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-slate-400 font-semibold">Connect Corridor To</label>
                  <select
                    value={connectToNode}
                    onChange={(e) => setConnectToNode(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100 text-xs focus:outline-none focus:ring-1 focus:ring-emerald-500"
                  >
                    <option value="">None (Isolated)</option>
                    {mapNodes.map((n) => (
                      <option key={n.id} value={n.id}>
                        Station {n.id} ({n.type || "dock"})
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <button
                type="submit"
                className="bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-1.5 px-3 rounded-lg border border-emerald-400 text-xs shadow transition flex items-center justify-center gap-1 mt-1"
              >
                <span>+ Add Station & Connect</span>
              </button>
            </form>

            {/* Form: Connect Travel Corridor */}
            <form
              onSubmit={handleAddCorridor}
              className="bg-slate-950/80 border border-slate-800 rounded-lg p-3 flex flex-col gap-2 shadow-inner"
            >
              <div className="font-bold text-slate-200 text-xs flex items-center gap-1.5">
                <span>🛣️ Connect Travel Corridor</span>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[10px] text-slate-400 font-semibold">From Station</label>
                  <select
                    value={edgeFrom}
                    onChange={(e) => setEdgeFrom(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100 text-xs"
                  >
                    <option value="">Select Station A</option>
                    {mapNodes.map((n) => (
                      <option key={n.id} value={n.id}>
                        Station {n.id}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-slate-400 font-semibold">To Station</label>
                  <select
                    value={edgeTo}
                    onChange={(e) => setEdgeTo(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100 text-xs"
                  >
                    <option value="">Select Station B</option>
                    {mapNodes.map((n) => (
                      <option key={n.id} value={n.id}>
                        Station {n.id}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <button
                type="submit"
                disabled={!edgeFrom || !edgeTo || edgeFrom === edgeTo}
                className="bg-sky-600 hover:bg-sky-500 text-white font-bold py-1.5 px-3 rounded-lg border border-sky-400 text-xs shadow transition disabled:opacity-40"
              >
                Connect Bidirectional Corridor
              </button>
            </form>

            {/* List: Existing Warehouse Stations */}
            <div className="bg-slate-950/80 border border-slate-800 rounded-lg p-2.5">
              <div className="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1.5 flex justify-between">
                <span>Warehouse Stations ({mapNodes.length})</span>
              </div>
              <div className="max-h-36 overflow-y-auto flex flex-col gap-1 text-xs">
                {mapNodes.map((n) => {
                  const isOcc = amrs.some((a) => a.current_node === n.id);
                  return (
                    <div
                      key={n.id}
                      className="flex justify-between items-center bg-slate-900/60 border border-slate-800/80 rounded px-2 py-1 text-[11px]"
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono font-bold text-slate-200">{n.id}</span>
                        <span className="text-[9px] px-1.5 py-0.2 rounded bg-slate-800 text-slate-400 border border-slate-700">
                          {n.type || "dock"}
                        </span>
                        <span className="text-slate-500 text-[10px]">
                          ({n.x}, {n.y})
                        </span>
                      </div>
                      <div className="flex items-center gap-1">
                        {isOcc ? (
                          <span className="text-[9px] text-amber-400 font-medium">Occupied</span>
                        ) : (
                          <button
                            onClick={() => handleDeleteStation(n.id)}
                            className="text-[9px] text-rose-400 hover:text-rose-200 px-1 py-0.5 rounded border border-rose-900 bg-rose-950/30 hover:bg-rose-900/40 transition"
                            title={`Delete station ${n.id}`}
                          >
                            ✕ Del
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
