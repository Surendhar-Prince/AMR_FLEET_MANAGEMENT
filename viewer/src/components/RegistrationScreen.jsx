import React, { useEffect, useState } from "react";
import { apiUrl } from "../api";

/**
 * RegistrationScreen
 *
 * Provides a dynamic "Sign In" vs "Create Account & Spawn AMR" tab switcher.
 * Dynamically loads start nodes from the backend map API without hardcoding.
 */
export function RegistrationScreen({ onRegistered }) {
  const [mode, setMode] = useState("signin"); // "signin" | "register"
  const [form, setForm] = useState({
    name: "",
    email: "",
    password: "",
    start_node: "",
  });
  const [nodes, setNodes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [assigned, setAssigned] = useState(null);

  // Fetch dynamic map nodes on mount
  useEffect(() => {
    fetch(apiUrl("/api/map"))
      .then((res) => res.json())
      .then((data) => {
        if (data.nodes && data.nodes.length > 0) {
          setNodes(data.nodes);
          setForm((prev) => ({
            ...prev,
            start_node: prev.start_node || data.nodes[0].id,
          }));
        }
      })
      .catch((err) => console.error("Failed to load dynamic map nodes:", err));
  }, []);

  function handleChange(e) {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    const email = form.email.trim().toLowerCase();
    const password = form.password;
    const name = form.name.trim();
    const start_node = form.start_node.trim();

    if (!email || !email.includes("@")) return setError("A valid email address is required.");
    if (!password || password.length < 6) return setError("Password must be at least 6 characters.");

    setLoading(true);

    try {
      if (mode === "signin") {
        // --- Sign In Existing User ---
        const resp = await fetch(apiUrl("/api/auth/login"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
        });
        const data = await resp.json();

        if (!resp.ok) {
          setError(data?.detail || "Sign in failed. Check your credentials.");
          setLoading(false);
          return;
        }

        setAssigned(data.amr_id);
        setTimeout(() => {
          onRegistered({
            userId: data.user_id,
            email: data.email,
            name: data.name,
            amrId: data.amr_id,
            amrs: data.amrs || [data.amr_id],
          });
        }, 1000);
      } else {
        // --- Register New Account & Spawn AMR ---
        if (!name) {
          setError("Name is required.");
          setLoading(false);
          return;
        }
        if (!start_node) {
          setError("Start node is required.");
          setLoading(false);
          return;
        }

        const resp = await fetch(apiUrl("/api/amrs/register"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, email, password, start_node }),
        });
        const data = await resp.json();

        if (!resp.ok) {
          setError(data?.detail || "Registration failed.");
          setLoading(false);
          return;
        }

        setAssigned(data.amr_id);
        setTimeout(() => {
          onRegistered({
            userId: data.user_id,
            email: email,
            name: name,
            amrId: data.amr_id,
            amrs: [data.amr_id],
          });
        }, 1200);
      }
    } catch (err) {
      setError("Network error — backend is unreachable.");
      setLoading(false);
    }
  }

  return (
    <div className="w-screen h-screen bg-slate-950 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_rgba(15,23,42,0)_0%,_rgba(2,6,23,0.95)_100%)] pointer-events-none" />

      <div className="relative z-10 w-full max-w-sm">
        {/* Header */}
        <div className="mb-6 text-center">
          <div className="inline-flex items-center gap-2 mb-3">
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-xs font-bold tracking-widest text-emerald-400 uppercase">
              Decentralized AMR Fleet
            </span>
          </div>
          <h1 className="text-2xl font-bold text-slate-100 tracking-tight">
            Fleet Access Portal
          </h1>
          <p className="text-slate-400 text-xs mt-1">
            Sign in or register to deploy up to 3 AMRs in the live warehouse
          </p>
        </div>

        {/* Card */}
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl shadow-2xl p-6 backdrop-blur-md">
          {assigned ? (
            <div className="flex flex-col items-center gap-3 py-6">
              <span className="w-5 h-5 rounded-full bg-emerald-500 animate-ping" />
              <p className="text-emerald-400 font-semibold text-sm tracking-wide">
                AMR Fleet Active:
              </p>
              <p className="text-2xl font-bold text-white tracking-widest uppercase">
                {assigned}
              </p>
              <p className="text-slate-400 text-xs mt-1">
                Entering 3D Digital Twin environment…
              </p>
            </div>
          ) : (
            <div>
              {/* Tab Switcher */}
              <div className="flex bg-slate-800/80 p-1 rounded-lg mb-5 border border-slate-700/50">
                <button
                  type="button"
                  onClick={() => { setMode("signin"); setError(null); }}
                  className={`flex-1 py-1.5 text-xs font-semibold rounded-md transition ${
                    mode === "signin"
                      ? "bg-indigo-600 text-white shadow"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  Sign In
                </button>
                <button
                  type="button"
                  onClick={() => { setMode("register"); setError(null); }}
                  className={`flex-1 py-1.5 text-xs font-semibold rounded-md transition ${
                    mode === "register"
                      ? "bg-indigo-600 text-white shadow"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  Create Account
                </button>
              </div>

              <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-3.5">
                {mode === "register" && (
                  <div className="flex flex-col gap-1">
                    <label className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                      Full Name
                    </label>
                    <input
                      type="text"
                      name="name"
                      value={form.name}
                      onChange={handleChange}
                      placeholder="e.g. Sriman Prince"
                      className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 transition"
                    />
                  </div>
                )}

                {/* Email */}
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                    Email Address
                  </label>
                  <input
                    type="email"
                    name="email"
                    value={form.email}
                    onChange={handleChange}
                    placeholder="user@warehouse.com"
                    className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 transition"
                  />
                </div>

                {/* Password */}
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                    Password
                  </label>
                  <input
                    type="password"
                    name="password"
                    value={form.password}
                    onChange={handleChange}
                    placeholder="Min 6 characters"
                    className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 transition"
                  />
                </div>

                {/* Dynamic Start Node Dropdown */}
                {mode === "register" && (
                  <div className="flex flex-col gap-1">
                    <label className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                      Spawn Station (Start Dock)
                    </label>
                    {nodes.length > 0 ? (
                      <select
                        name="start_node"
                        value={form.start_node}
                        onChange={handleChange}
                        className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500 transition"
                      >
                        {nodes.map((n) => (
                          <option key={n.id} value={n.id}>
                            Station {n.id} ({n.type || "Dock"})
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="text"
                        name="start_node"
                        value={form.start_node}
                        onChange={handleChange}
                        placeholder="e.g. n1 or N_00"
                        className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100"
                      />
                    )}
                  </div>
                )}

                {/* Error Banner */}
                {error && (
                  <div className="bg-red-950/70 border border-red-800/80 rounded-lg px-3 py-2 text-xs text-red-400">
                    {error}
                  </div>
                )}

                {/* Submit Button */}
                <button
                  type="submit"
                  disabled={loading}
                  className="mt-2 w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-bold text-xs tracking-wider py-2.5 rounded-lg border border-indigo-400/50 shadow-lg shadow-indigo-600/20 transition uppercase"
                >
                  {loading ? (
                    <span className="flex items-center justify-center gap-2">
                      <span className="w-3 h-3 rounded-full border-2 border-white/30 border-t-white animate-spin" />
                      Connecting…
                    </span>
                  ) : mode === "signin" ? (
                    "Sign In to Fleet"
                  ) : (
                    "Register & Spawn AMR"
                  )}
                </button>
              </form>
            </div>
          )}
        </div>

        <p className="text-center text-slate-500 text-[11px] mt-4">
          Decentralized P2P Consensus • Each user can operate up to 3 AMRs
        </p>
      </div>
    </div>
  );
}
