import React from "react";
import { Scene } from "./Scene";
import { FleetDashboard } from "./components/FleetDashboard";
import { useSimulationState } from "./useSimulationState";

export default function App() {
  const amrs = useSimulationState();

  return (
    <div className="w-screen h-screen flex bg-slate-950 text-slate-100 overflow-hidden font-sans select-none">
      {/* LEFT PANEL: Dedicated Fleet Control & Activity Dashboard */}
      <div className="w-[430px] h-full flex-shrink-0 border-r border-slate-800 bg-slate-900/90 flex flex-col z-20 shadow-2xl">
        <FleetDashboard amrs={amrs} isEmbedded={true} />
      </div>

      {/* RIGHT PANEL: 3D Warehouse Simulation Digital Twin */}
      <div className="flex-1 h-full relative bg-[#0b0f17]">
        <div className="absolute top-4 right-4 z-10 bg-slate-900/80 backdrop-blur border border-slate-700/60 rounded-lg px-3 py-1.5 text-xs text-slate-300 pointer-events-none flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
          <span>3D Real-Time Warehouse Twin</span>
        </div>
        <Scene amrs={amrs} />
      </div>
    </div>
  );
}
