import React, { useState, useEffect, useRef } from "react";
import { Scene } from "./Scene";
import { FleetDashboard } from "./components/FleetDashboard";
import { TelemetryMonitor } from "./components/TelemetryMonitor";
import { RegistrationScreen } from "./components/RegistrationScreen";
import { useSimulationState } from "./useSimulationState";
import { apiUrl } from "./api";

const SESSION_KEY = "amr_session";

function loadSession() {
  try {
    const raw = localStorage.getItem(SESSION_KEY) || sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && (parsed.userId || parsed.email || parsed.name)) {
      return parsed;
    }
    return null;
  } catch {
    return null;
  }
}


function saveSession(session) {
  try {
    if (session) {
      localStorage.setItem(SESSION_KEY, JSON.stringify(session));
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    } else {
      localStorage.removeItem(SESSION_KEY);
      sessionStorage.removeItem(SESSION_KEY);
    }
  } catch {
    /* storage unavailable */
  }
}

export default function App() {
  const amrs = useSimulationState();
  const [selectedNode, setSelectedNode] = useState(null);
  const [theme, setTheme] = useState("dark");
  const [showMonitorModal, setShowMonitorModal] = useState(false);

  // Session state
  const [session, setSession] = useState(() => loadSession());

  const handleAmrSpawned = (newAmrId, allAmrs) => {
    setSession((prevSess) => {
      const currentAmrs = prevSess?.amrs || (prevSess?.amrId ? [prevSess.amrId] : []);
      const updatedSess = {
        ...(prevSess || {}),
        amrId: newAmrId,
        amrs: allAmrs || (currentAmrs.includes(newAmrId) ? currentAmrs : [...currentAmrs, newAmrId]),
      };
      saveSession(updatedSess);
      return updatedSess;
    });
  };

  const handleAmrRemoved = (removedAmrId) => {
    setSession((prevSess) => {
      const remaining = (prevSess?.amrs || []).filter((id) => id !== removedAmrId);
      const updatedSess = {
        ...(prevSess || {}),
        amrs: remaining,
        amrId: remaining.length > 0 ? remaining[0] : null,
      };
      saveSession(updatedSess);
      return updatedSess;
    });
  };

  const handleSignOut = async () => {
    if (session?.email) {
      try {
        await fetch(apiUrl("/api/auth/logout"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: session.email, action: "despawn" }),
        });
      } catch (err) {
        console.error("Error signing out:", err);
      }
    }
    saveSession(null);
    setSession(null);
  };

  // Auto-restore AMR into simulation if session exists but backend simulation is empty
  const autoRestoredRef = useRef(false);
  useEffect(() => {
    if (session && !autoRestoredRef.current) {
      const timer = setTimeout(async () => {
        try {
          const res = await fetch(apiUrl("/api/amrs"));
          if (res.ok) {
            const activeList = await res.json();
            if (Array.isArray(activeList) && activeList.length === 0) {
              const spawnRes = await fetch(apiUrl("/api/amrs/spawn"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  email: session.email || "guest",
                  start_node: "n1",
                }),
              });
              if (spawnRes.ok) {
                const spawnData = await spawnRes.json();
                if (spawnData.amr_id) {
                  handleAmrSpawned(spawnData.amr_id, spawnData.amrs);
                }
              }
            }
          }
        } catch (e) {
          // ignore error
        }
        autoRestoredRef.current = true;
      }, 300);
      return () => clearTimeout(timer);
    }
  }, [session]);

  // If opened as /monitor, render standalone dedicated telemetry monitor
  if (window.location.pathname === "/monitor") {
    return (
      <div className="w-screen h-screen bg-slate-950 text-slate-100 overflow-hidden">
        <TelemetryMonitor amrs={amrs} isStandalone={true} />
      </div>
    );
  }

  // Show registration / sign-in screen if no active session
  if (!session) {
    return (
      <RegistrationScreen
        onRegistered={(result) => {
          const sess = {
            userId: result.userId,
            email: result.email,
            name: result.name,
            amrId: result.amrId,
            amrs: result.amrs || [result.amrId],
          };
          saveSession(sess);
          setSession(sess);
        }}
      />
    );
  }

  const isLight = theme === "light";

  return (
    <div className={`w-screen h-screen flex overflow-hidden ${isLight ? "bg-slate-100" : "bg-slate-950"}`}>


      {/* LEFT PANEL: Control, Dispatch, CBBA & Traffic HUD */}
      <div className="w-[480px] h-full flex-shrink-0 shadow-2xl z-10 border-r border-slate-800">
        <FleetDashboard
          amrs={amrs}
          selectedNode={selectedNode}
          onSelectNode={setSelectedNode}
          theme={theme}
          onToggleTheme={() => setTheme(theme === "light" ? "dark" : "light")}
          onOpenMonitor={() => setShowMonitorModal(true)}
          userSession={session}
          onAmrSpawned={handleAmrSpawned}
          onAmrRemoved={handleAmrRemoved}
        />
      </div>

      {/* RIGHT PANEL: 3D Warehouse Simulation Digital Twin */}
      <div className={`flex-1 h-full relative ${isLight ? "bg-[#f8fafc]" : "bg-[#0b0f17]"}`}>
        {/* Status HUD Header */}
        <div className="absolute top-4 right-4 z-10 flex items-center gap-2 pointer-events-auto">
          {/* Active AMRs Badge */}
          <div className="backdrop-blur bg-slate-900/90 border border-emerald-700/60 rounded-lg px-3 py-1.5 text-xs flex items-center gap-2 shadow-lg">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-slate-400 font-medium">Operator:</span>
            <span className="font-semibold text-emerald-400 tracking-wide uppercase">
              {session.name || session.email?.split("@")[0] || "Active User"}
            </span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-300 font-bold border border-emerald-700">
              {amrs.length}/6 AMRs
            </span>
          </div>

          <button
            onClick={() => window.open("/monitor", "_blank", "width=1280,height=820")}
            className="backdrop-blur bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold px-3 py-1.5 rounded-lg border border-indigo-400 shadow-lg shadow-indigo-600/30 transition flex items-center gap-1.5"
            title="Popout separate standalone monitor window"
          >
            <span>↗ Popout Monitor</span>
          </button>

          <button
            onClick={handleSignOut}
            className="backdrop-blur bg-red-900/80 hover:bg-red-800 text-red-200 text-xs font-semibold px-3 py-1.5 rounded-lg border border-red-700/80 transition"
            title="Sign out of current fleet session"
          >
            Sign Out
          </button>
        </div>

        {/* 3D Scene */}
        <Scene
          amrs={amrs}
          selectedNode={selectedNode}
          onSelectNode={setSelectedNode}
          theme={theme}
        />
      </div>

      {/* Embedded Fullscreen Telemetry Monitor Modal */}
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
