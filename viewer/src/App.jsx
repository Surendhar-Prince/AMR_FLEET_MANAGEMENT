import React, { useState } from "react";
import { Scene } from "./Scene";
import { FleetDashboard } from "./components/FleetDashboard";
import { TelemetryMonitor } from "./components/TelemetryMonitor";
import { useSimulationState } from "./useSimulationState";

export default function App() {
  const amrs = useSimulationState();
  const [selectedNode, setSelectedNode] = useState(null);
  const [theme, setTheme] = useState("light"); // "light" (Crisp White) | "dark"
  const [showMonitorModal, setShowMonitorModal] = useState(false);

  // If opened as /monitor, render standalone dedicated telemetry monitor
  if (window.location.pathname === "/monitor") {
    return (
      <div className="w-screen h-screen bg-slate-950 text-slate-100 overflow-hidden">
        <TelemetryMonitor amrs={amrs} isStandalone={true} />
      </div>
    );
  }

  const isLight = theme === "light";

  return (
    <div
      className={`w-screen h-screen flex overflow-hidden font-sans select-none ${
        isLight ? "bg-slate-100 text-slate-900" : "bg-slate-950 text-slate-100"
      }`}
    >
      {/* LEFT PANEL: Fleet Control & Dispatch Dashboard */}
      <div className="w-[440px] h-full flex-shrink-0 border-r border-slate-800/60 bg-slate-900/95 flex flex-col z-20 shadow-2xl">
        <FleetDashboard
          amrs={amrs}
          selectedNode={selectedNode}
          onSelectNode={setSelectedNode}
          theme={theme}
          onToggleTheme={() => setTheme(theme === "light" ? "dark" : "light")}
          onOpenMonitor={() => setShowMonitorModal(true)}
        />
      </div>

      {/* RIGHT PANEL: 3D Warehouse Simulation Digital Twin */}
      <div className={`flex-1 h-full relative ${isLight ? "bg-[#f8fafc]" : "bg-[#0b0f17]"}`}>
        {/* Status HUD Header */}
        <div className="absolute top-4 right-4 z-10 flex items-center gap-2 pointer-events-auto">
          <div
            className={`backdrop-blur border rounded-lg px-3 py-1.5 text-xs flex items-center gap-2 shadow-lg ${
              isLight
                ? "bg-white/90 border-slate-300 text-slate-700 shadow-slate-200"
                : "bg-slate-900/80 border-slate-700/60 text-slate-300 shadow-black/40"
            }`}
          >
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
            <span className="font-semibold tracking-wide">3D Real-Time Warehouse Twin</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-sky-100 text-sky-800 font-bold border border-sky-300">
              {theme.toUpperCase()} THEME
            </span>
          </div>

          <button
            onClick={() => window.open("/monitor", "_blank", "width=1280,height=820")}
            className="backdrop-blur bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold px-3 py-1.5 rounded-lg border border-indigo-400 shadow-lg shadow-indigo-600/30 transition flex items-center gap-1.5"
            title="Popout separate standalone monitor window"
          >
            <span>↗ Popout Monitor</span>
          </button>
        </div>

        {/* 3D Scene with Crisp Light / Dark Map */}
        <Scene
          amrs={amrs}
          selectedNode={selectedNode}
          onSelectNode={setSelectedNode}
          theme={theme}
        />
      </div>

      {/* Embedded Fullscreen Telemetry Monitor Modal (if opened via button) */}
      {showMonitorModal && (
        <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-md flex items-center justify-center p-6 animate-in fade-in duration-200">
          <div className="w-full max-w-6xl h-[85vh] bg-slate-950 border border-slate-800 rounded-2xl shadow-2xl flex flex-col overflow-hidden relative">
            <button
              onClick={() => setShowMonitorModal(false)}
              className="absolute top-4 right-4 z-50 text-slate-400 hover:text-white p-2 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-700 text-xs font-bold transition"
            >
              ✕ Close HUD
            </button>
            <TelemetryMonitor amrs={amrs} isStandalone={true} />
          </div>
        </div>
      )}
    </div>
  );
}
