import React, { useEffect, useMemo, useRef, useState } from "react";
import { apiUrl } from "../api";

export function TelemetryMonitor({ amrs, isStandalone = false }) {
  const [activeView, setActiveView] = useState("all"); // "all" | "machine" | "human" | "cbba" | "fleet" | "history"
  const [searchQuery, setSearchQuery] = useState("");
  const [filterType, setFilterType] = useState("ALL"); // "ALL" | "P2P" | "TRAFFIC" | "TASK" | "BATTERY" | "ERROR"
  const [taskFilterStatus, setTaskFilterStatus] = useState("ALL"); // "ALL" | "COMPLETED" | "IN_PROGRESS" | "UNASSIGNED" | "FAILED"
  const [isPaused, setIsPaused] = useState(false);
  const [taskHistory, setTaskHistory] = useState(() => {
    try {
      const stored = localStorage.getItem("amr_task_history");
      return stored ? JSON.parse(stored) : [];
    } catch {
      return [];
    }
  });
  const [logs, setLogs] = useState([
    {
      id: "boot-1",
      timestamp: new Date().toLocaleTimeString() + ".102",
      type: "P2P",
      source: "UDP_MESH",
      machine: "UDP::SOCKET_BOUND(port=9999, mode=SO_BROADCAST, tdma_slot=50ms)",
      human: "Decentralized P2P Mesh online! Listening for AMR datagrams on UDP Port 9999.",
    },
  ]);
  const [cbbaData, setCbbaData] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [networkStats, setNetworkStats] = useState({
    packetsSent: 14,
    packetsReceived: 18,
    latencyMs: 1.2,
    activeNodes: 1,
    meshProtocol: "UDP Broadcast / P2P Gossip (Port 9999)",
  });

  const prevDialogueCountRef = useRef(0);
  const prevAmrStatesRef = useRef({});
  const logScrollRef = useRef(null);

  // Live UDP Beacon heartbeat logger
  useEffect(() => {
    if (!amrs || amrs.length === 0) return;
    const beatInterval = setInterval(() => {
      amrs.forEach((amr) => {
        const isRemote = amr.is_remote;
        addLog({
          type: isRemote ? "P2P" : "TRAFFIC",
          source: amr.id,
          machine: `UDP::${isRemote ? "REMOTE_BEACON" : "AMR_BEACON"}(id=${amr.id}, node=${amr.current_node}, x=${amr.position?.x?.toFixed(1) ?? "0.0"}, y=${amr.position?.y?.toFixed(1) ?? "0.0"}, soc=${amr.battery_soc || 100}%)`,
          human: isRemote
            ? `📡 [Peer Laptop] ${amr.id} position synchronized via UDP Port 9999.`
            : `📍 ${amr.id} broadcasting coordinates at Station ${amr.current_node} [${amr.state_label}].`,
        });
      });
    }, 2000);
    return () => clearInterval(beatInterval);
  }, [amrs]);

  // Auto-scroll logs unless user scrolled up
  useEffect(() => {
    if (!isPaused && logScrollRef.current) {
      logScrollRef.current.scrollTop = logScrollRef.current.scrollHeight;
    }
  }, [logs, isPaused]);

  // Periodic Telemetry Polling
  useEffect(() => {
    const fetchTelemetry = async () => {
      try {
        const [cbbaRes, taskRes, historyRes] = await Promise.all([
          fetch(apiUrl("/api/cbba/state")),
          fetch(apiUrl("/api/tasks")),
          fetch(apiUrl("/api/tasks/history")),
        ]);

        if (cbbaRes.ok) {
          const cData = await cbbaRes.json();
          setCbbaData(cData);

          if (cData.network_telemetry) {
            setNetworkStats({
              packetsSent: Math.floor(cData.network_telemetry.total_packets_exchanged * 0.52),
              packetsReceived: cData.network_telemetry.total_packets_exchanged,
              latencyMs: cData.network_telemetry.latency_ms || 1.1,
              activeNodes: cData.network_telemetry.active_nodes_count || 4,
              meshProtocol: cData.network_telemetry.mesh_protocol,
            });

            // Ingest new P2P dialogues
            const dialogues = cData.network_telemetry.p2p_dialogues || [];
            if (dialogues.length > prevDialogueCountRef.current) {
              const newItems = dialogues.slice(prevDialogueCountRef.current);
              newItems.forEach((d) => {
                addLog({
                  type: "TRAFFIC",
                  source: d.source,
                  target: d.target,
                  machine: d.machine_protocol || `UDP::P2P_TRAFFIC(src=${d.source}, tgt=${d.target})`,
                  human: d.human_speech || `[${d.source} ➔ ${d.target}] "Yielding corridor passage."`,
                });
              });
              prevDialogueCountRef.current = dialogues.length;
            }
          }
        }

        if (taskRes.ok) {
          const tData = await taskRes.json();
          setTasks(tData);
        }

        if (historyRes.ok) {
          const hData = await historyRes.json();
          if (Array.isArray(hData)) {
            setTaskHistory((prev) => {
              // Merge existing with server history
              const map = new Map();
              prev.forEach((t) => map.set(t.id, t));
              hData.forEach((t) => map.set(t.id, t));
              const merged = Array.from(map.values());
              try {
                localStorage.setItem("amr_task_history", JSON.stringify(merged));
              } catch {}
              return merged;
            });
          }
        }
      } catch (err) {
        console.error("Telemetry fetch error:", err);
      }
    };

    fetchTelemetry();
    const interval = setInterval(fetchTelemetry, 800);
    return () => clearInterval(interval);
  }, []);

  // Track AMR lifecycle state transitions
  useEffect(() => {
    if (!amrs || amrs.length === 0) return;

    amrs.forEach((amr) => {
      const prev = prevAmrStatesRef.current[amr.id];
      if (prev) {
        // State change
        if (prev.state_label !== amr.state_label) {
          addLog({
            type: amr.state_label === "FAILED" ? "ERROR" : "P2P",
            source: amr.id,
            machine: `UDP::NODE_STATUS(id=${amr.id}, state=${amr.state_label}, node=${amr.current_node})`,
            human: `${amr.id}: "Transitioned to ${amr.state_label} at station ${amr.current_node}."`,
          });
        }
        // Collision / Near-miss
        if (!prev.colliding && amr.colliding) {
          addLog({
            type: "ERROR",
            source: amr.id,
            machine: `UDP::COLLISION_WARN(id=${amr.id}, loc=(${amr.position.x.toFixed(1)}, ${amr.position.y.toFixed(1)}))`,
            human: `⚠️ ${amr.id}: "Safety boundary proximity triggered at (${amr.position.x.toFixed(1)}, ${amr.position.y.toFixed(1)})."`,
          });
        }
      }
      prevAmrStatesRef.current[amr.id] = { ...amr };
    });
  }, [amrs]);

  const addLog = (entry) => {
    if (isPaused) return;
    setLogs((prev) => [
      ...prev.slice(-300), // Keep last 300 logs for high performance
      {
        id: "log-" + Date.now() + "-" + Math.random().toString(36).substr(2, 5),
        timestamp: new Date().toLocaleTimeString() + "." + Math.floor(Math.random() * 900 + 100),
        ...entry,
      },
    ]);
  };

  const filteredLogs = useMemo(() => {
    return logs.filter((log) => {
      if (filterType !== "ALL" && log.type !== filterType) return false;
      if (!searchQuery) return true;
      const q = searchQuery.toLowerCase();
      return (
        (log.machine && log.machine.toLowerCase().includes(q)) ||
        (log.human && log.human.toLowerCase().includes(q)) ||
        (log.source && log.source.toLowerCase().includes(q))
      );
    });
  }, [logs, filterType, searchQuery]);

  const filteredTasks = useMemo(() => {
    return (taskHistory || []).filter((t) => {
      if (taskFilterStatus !== "ALL" && t.status !== taskFilterStatus) return false;
      if (!searchQuery) return true;
      const q = searchQuery.toLowerCase();
      return (
        (t.id && t.id.toLowerCase().includes(q)) ||
        (t.pickup_node && t.pickup_node.toLowerCase().includes(q)) ||
        (t.dropoff_node && t.dropoff_node.toLowerCase().includes(q)) ||
        (t.assigned_to && t.assigned_to.toLowerCase().includes(q)) ||
        (t.status && t.status.toLowerCase().includes(q))
      );
    });
  }, [taskHistory, taskFilterStatus, searchQuery]);

  // Statistics calculation for Task History
  const taskStats = useMemo(() => {
    const total = taskHistory.length;
    const completed = taskHistory.filter((t) => t.status === "COMPLETED").length;
    const inFlight = taskHistory.filter((t) => t.status === "IN_PROGRESS" || t.status === "ASSIGNED").length;
    const failed = taskHistory.filter((t) => t.status === "FAILED").length;
    const completedWithDuration = taskHistory.filter((t) => t.status === "COMPLETED" && t.duration_seconds);
    const avgDuration =
      completedWithDuration.length > 0
        ? (
            completedWithDuration.reduce((acc, t) => acc + (t.duration_seconds || 0), 0) /
            completedWithDuration.length
          ).toFixed(1)
        : "—";
    const successRate = total > 0 ? ((completed / (completed + failed || 1)) * 100).toFixed(0) : "100";

    return { total, completed, inFlight, failed, avgDuration, successRate };
  }, [taskHistory]);

  const handleClearCompletedTasks = async () => {
    try {
      await fetch(apiUrl("/api/tasks/clear"), { method: "POST" });
      const hRes = await fetch(apiUrl("/api/tasks/history"));
      if (hRes.ok) {
        const hData = await hRes.json();
        setTaskHistory(hData);
        localStorage.setItem("amr_task_history", JSON.stringify(hData));
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleResetAllTasks = async () => {
    try {
      await fetch(apiUrl("/api/tasks/clear?include_active=true"), { method: "POST" });
      setTaskHistory([]);
      localStorage.removeItem("amr_task_history");
      const hRes = await fetch(apiUrl("/api/tasks/history"));
      if (hRes.ok) {
        const hData = await hRes.json();
        setTaskHistory(hData);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleCancelTask = async (taskId) => {
    try {
      await fetch(apiUrl(`/api/tasks/${taskId}`), { method: "DELETE" });
      const hRes = await fetch(apiUrl("/api/tasks/history"));
      if (hRes.ok) {
        const hData = await hRes.json();
        setTaskHistory(hData);
        localStorage.setItem("amr_task_history", JSON.stringify(hData));
      }
    } catch (e) {
      console.error(e);
    }
  };

  const exportTaskHistory = () => {
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(taskHistory, null, 2));
    const downloadAnchor = document.createElement("a");
    downloadAnchor.setAttribute("href", dataStr);
    downloadAnchor.setAttribute("download", `amr_task_history_${Date.now()}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  const exportLogs = () => {
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(logs, null, 2));
    const downloadAnchor = document.createElement("a");
    downloadAnchor.setAttribute("href", dataStr);
    downloadAnchor.setAttribute("download", `amr_fleet_telemetry_${Date.now()}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  return (
    <div className={`w-full h-full flex flex-col bg-slate-950 text-slate-100 font-mono select-none overflow-hidden ${isStandalone ? "p-4" : "p-2"}`}>
      {/* Top Telemetry Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-slate-800">
        <div className="flex items-center gap-3">
          <div className="w-3 h-3 rounded-full bg-cyan-400 animate-ping"></div>
          <div>
            <div className="text-sm font-bold tracking-wider text-cyan-300 flex items-center gap-2">
              <span>DECENTRALIZED P2P TELEMETRY MONITOR</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-950 text-cyan-400 border border-cyan-800">
                UDP PORT 9999
              </span>
            </div>
            <div className="text-[11px] text-slate-400">
              Round-Robin TDMA Mesh • Live Node Consensus & Task History Diagnostics
            </div>
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsPaused(!isPaused)}
            className={`px-2.5 py-1 text-xs rounded border transition font-sans ${
              isPaused
                ? "bg-amber-950/80 border-amber-600 text-amber-300 animate-pulse"
                : "bg-slate-900 border-slate-700 text-slate-300 hover:bg-slate-800"
            }`}
          >
            {isPaused ? "▶ RESUME STREAM" : "⏸ PAUSE STREAM"}
          </button>
          <button
            onClick={() => setLogs([])}
            className="px-2.5 py-1 text-xs rounded bg-slate-900 border border-slate-700 text-slate-300 hover:bg-slate-800 transition font-sans"
          >
            CLEAR LOGS
          </button>
          <button
            onClick={activeView === "history" ? exportTaskHistory : exportLogs}
            className="px-2.5 py-1 text-xs rounded bg-cyan-950 border border-cyan-700 text-cyan-300 hover:bg-cyan-900 transition font-sans flex items-center gap-1"
          >
            <span>📥 {activeView === "history" ? "EXPORT TASKS" : "EXPORT JSON"}</span>
          </button>
          {!isStandalone && (
            <button
              onClick={() => window.open("/monitor", "_blank", "width=1280,height=820")}
              className="px-3 py-1 text-xs rounded bg-indigo-600 hover:bg-indigo-500 text-white font-sans font-medium transition flex items-center gap-1 shadow-lg shadow-indigo-600/30"
              title="Open standalone telemetry monitor in separate screen"
            >
              <span>↗ POPOUT MONITOR</span>
            </button>
          )}
        </div>
      </div>

      {/* Network Health KPI Bar */}
      <div className="grid grid-cols-2 md:grid-cols-6 gap-2 my-3 font-sans">
        <div className="bg-slate-900/90 border border-slate-800 rounded-lg p-2 flex flex-col">
          <span className="text-[10px] text-slate-400 uppercase font-semibold">Mesh Status</span>
          <span className="text-sm font-bold text-emerald-400 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
            ACTIVE PEER MESH
          </span>
        </div>
        <div className="bg-slate-900/90 border border-slate-800 rounded-lg p-2 flex flex-col">
          <span className="text-[10px] text-slate-400 uppercase font-semibold">Active Nodes</span>
          <span className="text-sm font-bold text-cyan-300">{networkStats.activeNodes} Nodes Online</span>
        </div>
        <div className="bg-slate-900/90 border border-slate-800 rounded-lg p-2 flex flex-col">
          <span className="text-[10px] text-slate-400 uppercase font-semibold">Packets Exchanged</span>
          <span className="text-sm font-bold text-indigo-300">{networkStats.packetsReceived} datagrams</span>
        </div>
        <div className="bg-slate-900/90 border border-slate-800 rounded-lg p-2 flex flex-col">
          <span className="text-[10px] text-slate-400 uppercase font-semibold">In-Flight Tasks</span>
          <span className="text-sm font-bold text-amber-300">
            {tasks.filter((t) => t.status !== "COMPLETED").length} Active
          </span>
        </div>
        <div className="bg-slate-900/90 border border-slate-800 rounded-lg p-2 flex flex-col">
          <span className="text-[10px] text-slate-400 uppercase font-semibold">Completed Missions</span>
          <span className="text-sm font-bold text-emerald-400 flex items-center gap-1">
            <span>✅</span>
            <span>{taskStats.completed} Delivered</span>
          </span>
        </div>
        <div className="bg-slate-900/90 border border-slate-800 rounded-lg p-2 flex flex-col">
          <span className="text-[10px] text-slate-400 uppercase font-semibold">Task History</span>
          <span className="text-sm font-bold text-purple-300">{taskHistory.length} Recorded</span>
        </div>
      </div>

      {/* Nav Tabs & Filter Bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 pb-2 mb-2 border-b border-slate-800/80 font-sans">
        <div className="flex items-center gap-1 bg-slate-900 p-1 rounded-lg border border-slate-800">
          <button
            onClick={() => setActiveView("all")}
            className={`px-3 py-1 text-xs rounded-md transition font-medium ${
              activeView === "all" ? "bg-cyan-600 text-white shadow" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            All Feeds
          </button>
          <button
            onClick={() => setActiveView("history")}
            className={`px-3 py-1 text-xs rounded-md transition font-medium flex items-center gap-1.5 ${
              activeView === "history" ? "bg-emerald-600 text-white shadow" : "text-emerald-400 hover:text-emerald-200"
            }`}
          >
            <span>📋 Task History</span>
            <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-emerald-950 text-emerald-200 border border-emerald-800 font-bold">
              {taskHistory.length}
            </span>
          </button>
          <button
            onClick={() => setActiveView("cbba")}
            className={`px-3 py-1 text-xs rounded-md transition font-medium ${
              activeView === "cbba" ? "bg-cyan-600 text-white shadow" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Live CBBA Matrix
          </button>
          <button
            onClick={() => setActiveView("fleet")}
            className={`px-3 py-1 text-xs rounded-md transition font-medium ${
              activeView === "fleet" ? "bg-cyan-600 text-white shadow" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Fleet Node Status
          </button>
          <button
            onClick={() => setActiveView("human")}
            className={`px-3 py-1 text-xs rounded-md transition font-medium ${
              activeView === "human" ? "bg-cyan-600 text-white shadow" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            NLP Dialogues
          </button>
          <button
            onClick={() => setActiveView("machine")}
            className={`px-3 py-1 text-xs rounded-md transition font-medium ${
              activeView === "machine" ? "bg-cyan-600 text-white shadow" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            UDP Packets
          </button>
        </div>

        {/* Filter / Search inputs */}
        {activeView === "history" ? (
          <div className="flex items-center gap-2">
            <select
              value={taskFilterStatus}
              onChange={(e) => setTaskFilterStatus(e.target.value)}
              className="bg-slate-900 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1 focus:outline-none focus:border-emerald-500"
            >
              <option value="ALL">All Statuses</option>
              <option value="COMPLETED">✅ Completed</option>
              <option value="IN_PROGRESS">🚚 In-Progress</option>
              <option value="UNASSIGNED">⏳ Unassigned</option>
              <option value="FAILED">❌ Failed / Cancelled</option>
            </select>
            <input
              type="text"
              placeholder="Search tasks by ID / dock / AMR..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded px-2.5 py-1 w-56 placeholder-slate-500 focus:outline-none focus:border-emerald-500"
            />
          </div>
        ) : (activeView === "all" || activeView === "machine" || activeView === "human") && (
          <div className="flex items-center gap-2">
            <select
              value={filterType}
              onChange={(e) => setFilterType(e.target.value)}
              className="bg-slate-900 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1 focus:outline-none focus:border-cyan-500"
            >
              <option value="ALL">All Types</option>
              <option value="TRAFFIC">Traffic & Yielding</option>
              <option value="P2P">P2P Auctions</option>
              <option value="TASK">Task Lifecycle</option>
              <option value="ERROR">Warnings & Conflicts</option>
            </select>
            <input
              type="text"
              placeholder="Search telemetry..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded px-2.5 py-1 w-44 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
            />
          </div>
        )}
      </div>

      {/* MAIN CONTENT AREA */}
      <div className="flex-1 overflow-hidden relative">
        {/* VIEW 1: Live Log Stream (All, Machine, Human) */}
        {(activeView === "all" || activeView === "machine" || activeView === "human") && (
          <div
            ref={logScrollRef}
            className="w-full h-full overflow-y-auto pr-2 space-y-1.5 text-xs select-text font-mono"
          >
            {filteredLogs.length === 0 ? (
              <div className="h-full flex items-center justify-center text-slate-500 italic">
                Awaiting UDP telemetry datagrams on port 9999...
              </div>
            ) : (
              filteredLogs.map((log) => (
                <div
                  key={log.id}
                  className={`p-2 rounded border transition ${
                    log.type === "ERROR"
                      ? "bg-rose-950/40 border-rose-800/60 text-rose-200"
                      : log.type === "TRAFFIC"
                      ? "bg-amber-950/30 border-amber-800/40 text-amber-200"
                      : "bg-slate-900/70 border-slate-800/70 text-slate-300"
                  }`}
                >
                  <div className="flex items-center justify-between text-[10px] text-slate-400 mb-1">
                    <span className="flex items-center gap-2">
                      <span className="text-cyan-400 font-semibold">{log.timestamp}</span>
                      <span
                        className={`px-1 py-0.2 rounded font-sans font-bold text-[9px] ${
                          log.type === "ERROR"
                            ? "bg-rose-900 text-rose-300"
                            : log.type === "TRAFFIC"
                            ? "bg-amber-900 text-amber-300"
                            : "bg-cyan-900 text-cyan-300"
                        }`}
                      >
                        {log.type}
                      </span>
                      {log.source && <span className="text-slate-400">Node: {log.source}</span>}
                    </span>
                    {log.target && <span className="text-slate-500">➔ Target: {log.target}</span>}
                  </div>

                  {/* Machine Protocol Line */}
                  {(activeView === "all" || activeView === "machine") && log.machine && (
                    <div className="font-mono text-cyan-300/90 break-all bg-slate-950/60 p-1 rounded my-0.5 border border-slate-800/40">
                      {log.machine}
                    </div>
                  )}

                  {/* Natural Language Line */}
                  {(activeView === "all" || activeView === "human") && log.human && (
                    <div className="font-sans text-slate-200 pl-1 border-l-2 border-cyan-500/50 mt-1">
                      {log.human}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        )}

        {/* VIEW 2: Dynamic Live CBBA Mathematical Matrix */}
        {activeView === "cbba" && (
          <div className="w-full h-full overflow-y-auto space-y-4 p-1">
            <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-3">
              <h3 className="text-xs font-bold text-cyan-400 uppercase tracking-wide mb-2 flex items-center justify-between font-sans">
                <span>Decentralized Consensus Bid Matrix (Live Market Auction)</span>
                <span className="text-[11px] text-slate-400 font-normal">Updated per TDMA slot</span>
              </h3>
              {cbbaData?.bid_matrix && cbbaData.bid_matrix.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs text-left border-collapse font-mono">
                    <thead>
                      <tr className="bg-slate-800/80 text-slate-300 border-b border-slate-700">
                        <th className="p-2">Task ID</th>
                        <th className="p-2">Route</th>
                        <th className="p-2">Priority</th>
                        <th className="p-2">Status</th>
                        {Object.keys(cbbaData.bid_matrix[0]?.bids || {}).map((amrId) => (
                          <th key={amrId} className="p-2 text-center text-cyan-300">
                            {amrId} Bid
                          </th>
                        ))}
                        <th className="p-2 text-right">Winning AMR</th>
                      </tr>
                    </thead>
                    <tbody>
                      {cbbaData.bid_matrix.map((row) => (
                        <tr key={row.task_id} className="border-b border-slate-800/60 hover:bg-slate-800/30">
                          <td className="p-2 font-bold text-slate-200">{row.task_id}</td>
                          <td className="p-2 text-slate-400">
                            {row.pickup_node} ➔ {row.dropoff_node}
                          </td>
                          <td className="p-2">
                            <span
                              className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                row.priority === 3
                                  ? "bg-rose-950 text-rose-300 border border-rose-800"
                                  : row.priority === 2
                                  ? "bg-amber-950 text-amber-300 border border-amber-800"
                                  : "bg-slate-800 text-slate-300"
                              }`}
                            >
                              P{row.priority}
                            </span>
                          </td>
                          <td className="p-2">
                            <span
                              className={`px-1.5 py-0.5 rounded text-[10px] ${
                                row.status === "COMPLETED"
                                  ? "bg-emerald-950 text-emerald-300"
                                  : row.status === "IN_PROGRESS"
                                  ? "bg-cyan-950 text-cyan-300"
                                  : "bg-slate-800 text-slate-400"
                              }`}
                            >
                              {row.status}
                            </span>
                          </td>
                          {Object.entries(row.bids || {}).map(([amrId, bidVal]) => {
                            const isWinner = row.winner === amrId;
                            return (
                              <td
                                key={amrId}
                                className={`p-2 text-center ${
                                  isWinner
                                    ? "bg-cyan-950/60 text-cyan-300 font-bold border border-cyan-800/60 rounded"
                                    : "text-slate-500"
                                }`}
                              >
                                {bidVal > 0 ? bidVal.toFixed(2) : "—"}
                              </td>
                            );
                          })}
                          <td className="p-2 text-right font-bold text-cyan-400">
                            {row.winner ? `🏆 ${row.winner}` : "Pending Consensus"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-slate-500 text-xs italic py-4 text-center">
                  No tasks currently active in the auction bundle.
                </div>
              )}
            </div>
          </div>
        )}

        {/* VIEW 3: Fleet Edge Companion Computers Status */}
        {activeView === "fleet" && (
          <div className="w-full h-full overflow-y-auto space-y-3 p-1 font-sans">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {(amrs || []).map((amr) => (
                <div
                  key={amr.id}
                  className={`p-3 rounded-lg border flex flex-col justify-between ${
                    amr.is_remote
                      ? "bg-cyan-950/30 border-cyan-800 text-cyan-100"
                      : amr.colliding || amr.state_label === "FAILED"
                      ? "bg-rose-950/30 border-rose-800 text-rose-100"
                      : "bg-slate-900/80 border-slate-800 text-slate-200"
                  }`}
                >
                  <div className="flex items-center justify-between pb-2 border-b border-slate-800/80">
                    <div className="flex items-center gap-2">
                      <span className="w-2.5 h-2.5 rounded-full bg-emerald-400"></span>
                      <span className="font-bold text-sm tracking-wide">{amr.id}</span>
                      {amr.is_remote && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-900 text-cyan-200 border border-cyan-700">
                          PEER LAPTOP
                        </span>
                      )}
                    </div>
                    <span
                      className={`text-xs px-2 py-0.5 rounded font-medium ${
                        amr.state_label === "TRANSIT"
                          ? "bg-cyan-900 text-cyan-200"
                          : amr.state_label === "YIELDING"
                          ? "bg-amber-900 text-amber-200"
                          : amr.state_label === "CHARGING"
                          ? "bg-emerald-900 text-emerald-200"
                          : "bg-slate-800 text-slate-300"
                      }`}
                    >
                      {amr.state_label}
                    </span>
                  </div>

                  <div className="grid grid-cols-2 gap-2 my-2.5 text-xs">
                    <div>
                      <span className="text-slate-400">Current Station:</span>{" "}
                      <span className="font-mono font-bold text-slate-200">{amr.current_node}</span>
                    </div>
                    <div>
                      <span className="text-slate-400">Battery Level:</span>{" "}
                      <span className="font-mono font-bold text-emerald-400">{amr.battery_soc?.toFixed(1) || 100}%</span>
                    </div>
                    <div>
                      <span className="text-slate-400">Active Task:</span>{" "}
                      <span className="font-mono text-cyan-300">
                        {amr.active_task
                          ? amr.has_payload || amr.subtask === "DROPOFF"
                            ? `${amr.active_task} (Loaded 📦)`
                            : `${amr.active_task} (Pickup En Route)`
                          : "None (Idle)"}
                      </span>
                    </div>
                    <div>
                      <span className="text-slate-400">Position (X, Y):</span>{" "}
                      <span className="font-mono text-slate-300">
                        {amr.position.x.toFixed(1)}, {amr.position.y.toFixed(1)}
                      </span>
                    </div>
                  </div>

                  {/* Battery Bar */}
                  <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                    <div
                      className={`h-full ${
                        (amr.battery_soc || 100) < 25
                          ? "bg-rose-500"
                          : (amr.battery_soc || 100) < 50
                          ? "bg-amber-500"
                          : "bg-emerald-500"
                      }`}
                      style={{ width: `${amr.battery_soc || 100}%` }}
                    ></div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* VIEW 4: Task Execution History (Mission Logs & Lifecycle Persistence) */}
        {activeView === "history" && (
          <div className="w-full h-full overflow-y-auto space-y-3 p-1 font-sans">
            {/* Mission Performance Summary Bar */}
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
              <div className="bg-slate-900/90 border border-slate-800 rounded-lg p-2.5 flex flex-col">
                <span className="text-[10px] text-slate-400 uppercase font-semibold">Total Dispatched</span>
                <span className="text-base font-bold text-slate-100">{taskStats.total} Tasks</span>
              </div>
              <div className="bg-slate-900/90 border border-emerald-800/60 rounded-lg p-2.5 flex flex-col">
                <span className="text-[10px] text-emerald-400 uppercase font-semibold">Delivered (Completed)</span>
                <span className="text-base font-bold text-emerald-400 flex items-center gap-1.5">
                  <span>✅</span>
                  <span>{taskStats.completed}</span>
                  <span className="text-xs font-normal text-emerald-500">({taskStats.successRate}%)</span>
                </span>
              </div>
              <div className="bg-slate-900/90 border border-amber-800/60 rounded-lg p-2.5 flex flex-col">
                <span className="text-[10px] text-amber-400 uppercase font-semibold">In-Flight Active</span>
                <span className="text-base font-bold text-amber-400 flex items-center gap-1.5">
                  <span>🚚</span>
                  <span>{taskStats.inFlight}</span>
                </span>
              </div>
              <div className="bg-slate-900/90 border border-slate-800 rounded-lg p-2.5 flex flex-col">
                <span className="text-[10px] text-slate-400 uppercase font-semibold">Avg Delivery Time</span>
                <span className="text-base font-bold text-cyan-300">
                  {taskStats.avgDuration !== "—" ? `${taskStats.avgDuration}s` : "—"}
                </span>
              </div>
              <div className="bg-slate-900/90 border border-slate-800 rounded-lg p-2.5 flex flex-col">
                <span className="text-[10px] text-slate-400 uppercase font-semibold">Failed / Blocked</span>
                <span className="text-base font-bold text-rose-400">{taskStats.failed}</span>
              </div>
            </div>

            {/* Action Bar */}
            <div className="flex flex-wrap items-center justify-between gap-2 bg-slate-900/80 p-2.5 rounded-lg border border-slate-800">
              <div className="text-xs text-slate-300 font-semibold flex items-center gap-2">
                <span>Mission Execution History Log</span>
                <span className="text-[10px] text-slate-500 font-normal">
                  (Showing {filteredTasks.length} of {taskHistory.length} recorded tasks)
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleClearCompletedTasks}
                  className="px-2.5 py-1 text-xs rounded bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition"
                  title="Clear finished tasks from the active pool"
                >
                  🧹 Clear Finished
                </button>
                <button
                  onClick={handleResetAllTasks}
                  className="px-2.5 py-1 text-xs rounded bg-rose-950/80 hover:bg-rose-900 text-rose-200 border border-rose-800 transition"
                  title="Reset all tasks and return AMRs to idle"
                >
                  🛑 Reset All
                </button>
                <button
                  onClick={exportTaskHistory}
                  className="px-2.5 py-1 text-xs rounded bg-emerald-950 hover:bg-emerald-900 text-emerald-300 border border-emerald-800 transition flex items-center gap-1"
                >
                  <span>📥 Export JSON</span>
                </button>
              </div>
            </div>

            {/* Task History Table */}
            <div className="bg-slate-900/90 border border-slate-800 rounded-lg overflow-hidden">
              {filteredTasks.length === 0 ? (
                <div className="p-8 text-center text-slate-500 text-xs italic">
                  No task execution records matching your criteria. Dispatched tasks will automatically appear here with complete delivery metrics.
                </div>
              ) : (
                <div className="overflow-x-auto max-h-[420px]">
                  <table className="w-full text-xs text-left border-collapse font-mono">
                    <thead className="sticky top-0 bg-slate-950 text-slate-300 border-b border-slate-800 z-10">
                      <tr>
                        <th className="p-2.5">Task ID</th>
                        <th className="p-2.5">Pickup ➔ Dropoff</th>
                        <th className="p-2.5">Priority</th>
                        <th className="p-2.5">Assigned AMR</th>
                        <th className="p-2.5">Status</th>
                        <th className="p-2.5">Dispatched At</th>
                        <th className="p-2.5">Duration</th>
                        <th className="p-2.5 text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/60">
                      {filteredTasks.map((t) => {
                        const isCompleted = t.status === "COMPLETED";
                        const isInProgress = t.status === "IN_PROGRESS" || t.status === "ASSIGNED";
                        const isFailed = t.status === "FAILED";

                        return (
                          <tr key={t.id} className="hover:bg-slate-800/40 transition-colors">
                            <td className="p-2.5 font-bold text-slate-200">{t.id}</td>
                            <td className="p-2.5 text-slate-300">
                              <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-200 font-bold border border-slate-700">
                                {t.pickup_node}
                              </span>
                              <span className="text-slate-500 mx-1.5">➔</span>
                              <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-200 font-bold border border-slate-700">
                                {t.dropoff_node}
                              </span>
                            </td>
                            <td className="p-2.5">
                              <span
                                className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                  t.priority === 3
                                    ? "bg-rose-950 text-rose-300 border border-rose-800"
                                    : t.priority === 2
                                    ? "bg-amber-950 text-amber-300 border border-amber-800"
                                    : "bg-slate-800 text-slate-300 border border-slate-700"
                                }`}
                              >
                                P{t.priority || 1}
                              </span>
                            </td>
                            <td className="p-2.5">
                              {t.assigned_to ? (
                                <span className="px-2 py-0.5 rounded bg-indigo-950 text-indigo-300 border border-indigo-800 font-bold text-[11px]">
                                  🤖 {t.assigned_to}
                                </span>
                              ) : (
                                <span className="text-slate-500 italic">Unallocated</span>
                              )}
                            </td>
                            <td className="p-2.5">
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-bold inline-flex items-center gap-1 ${
                                  isCompleted
                                    ? "bg-emerald-950 text-emerald-300 border border-emerald-800"
                                    : isInProgress
                                    ? "bg-cyan-950 text-cyan-300 border border-cyan-800"
                                    : isFailed
                                    ? "bg-rose-950 text-rose-300 border border-rose-800"
                                    : "bg-amber-950 text-amber-300 border border-amber-800"
                                }`}
                              >
                                {isCompleted && "✅ COMPLETED"}
                                {isInProgress && "🚚 IN_PROGRESS"}
                                {isFailed && "❌ FAILED"}
                                {!isCompleted && !isInProgress && !isFailed && "⏳ UNASSIGNED"}
                              </span>
                            </td>
                            <td className="p-2.5 text-slate-400">
                              {t.formatted_time || (t.created_at ? new Date(t.created_at * 1000).toLocaleTimeString() : "—")}
                            </td>
                            <td className="p-2.5 text-cyan-300 font-medium">
                              {t.duration_seconds !== undefined && t.duration_seconds !== null
                                ? `${t.duration_seconds}s`
                                : isCompleted
                                ? "Finished"
                                : isInProgress
                                ? "In-flight"
                                : "—"}
                            </td>
                            <td className="p-2.5 text-right">
                              {isInProgress || t.status === "UNASSIGNED" ? (
                                <button
                                  onClick={() => handleCancelTask(t.id)}
                                  className="px-2 py-0.5 rounded bg-rose-950 hover:bg-rose-900 text-rose-300 border border-rose-800 text-[10px] font-bold transition"
                                  title="Cancel in-flight mission"
                                >
                                  Cancel
                                </button>
                              ) : (
                                <span className="text-slate-600 text-[10px]">—</span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
