import React, { useState } from "react";
import { apiUrl } from "../api";

/**
 * RegistrationScreen
 *
 * Shown before the main environment.  Matches the existing dark-slate /
 * emerald / sky / indigo dashboard visual language.
 *
 * On success, calls onRegistered({ userId, amrId, profile, amr }).
 */
export function RegistrationScreen({ onRegistered }) {
  const [form, setForm] = useState({
    name: "",
    email: "",
    password: "",
    start_node: "",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [assigned, setAssigned] = useState(null); // amr_id shown briefly

  function handleChange(e) {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    const name = form.name.trim();
    const email = form.email.trim().toLowerCase();
    const password = form.password;
    const start_node = form.start_node.trim();

    // Client-side pre-validation
    if (!name) return setError("Name is required.");
    if (!email || !email.includes("@")) return setError("A valid email is required.");
    if (!password || password.length < 6)
      return setError("Password must be at least 6 characters.");
    if (!start_node) return setError("Start node is required.");

    setLoading(true);
    try {
      const resp = await fetch(apiUrl("/api/amrs/register"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, email, password, start_node }),
      });

      const data = await resp.json();

      if (!resp.ok) {
        // Surface the server's validation message
        const msg =
          data?.detail ||
          (typeof data === "string" ? data : "Registration failed.");
        setError(msg);
        setLoading(false);
        return;
      }

      // Show the assigned AMR id for a moment before entering
      setAssigned(data.amr_id);
      setTimeout(() => {
        onRegistered({
          userId: data.user_id,
          amrId: data.amr_id,
          profile: data.profile,
          amr: data.amr,
        });
      }, 1400);
    } catch (err) {
      setError("Network error — is the backend reachable?");
      setLoading(false);
    }
  }

  return (
    <div className="w-screen h-screen bg-slate-950 flex items-center justify-center p-4">
      {/* Ambient grid background — matches existing scene HUD vibe */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_rgba(15,23,42,0)_0%,_rgba(2,6,23,0.9)_100%)] pointer-events-none" />

      <div className="relative z-10 w-full max-w-sm">
        {/* Header */}
        <div className="mb-6 text-center">
          <div className="inline-flex items-center gap-2 mb-3">
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-xs font-bold tracking-widest text-emerald-400 uppercase">
              AMR Fleet Management
            </span>
          </div>
          <h1 className="text-2xl font-bold text-slate-100 tracking-tight">
            Fleet Access
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            Register to enter the shared warehouse environment
          </p>
        </div>

        {/* Card */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-2xl p-6">
          {assigned ? (
            /* Success state */
            <div className="flex flex-col items-center gap-3 py-6">
              <span className="w-4 h-4 rounded-full bg-emerald-500 animate-ping" />
              <p className="text-emerald-400 font-semibold text-sm tracking-wide">
                AMR Assigned:
              </p>
              <p className="text-2xl font-bold text-white tracking-widest uppercase">
                {assigned}
              </p>
              <p className="text-slate-400 text-xs mt-1">
                Entering environment…
              </p>
            </div>
          ) : (
            <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
              {/* Name */}
              <div className="flex flex-col gap-1">
                <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  Name
                </label>
                <input
                  type="text"
                  name="name"
                  value={form.name}
                  onChange={handleChange}
                  placeholder="Your name"
                  autoComplete="name"
                  className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-sky-500 focus:border-sky-500 transition"
                />
              </div>

              {/* Email */}
              <div className="flex flex-col gap-1">
                <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  Email
                </label>
                <input
                  type="email"
                  name="email"
                  value={form.email}
                  onChange={handleChange}
                  placeholder="you@example.com"
                  autoComplete="email"
                  className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-sky-500 focus:border-sky-500 transition"
                />
              </div>

              {/* Password */}
              <div className="flex flex-col gap-1">
                <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  Password
                </label>
                <input
                  type="password"
                  name="password"
                  value={form.password}
                  onChange={handleChange}
                  placeholder="Min 6 characters"
                  autoComplete="new-password"
                  className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-sky-500 focus:border-sky-500 transition"
                />
              </div>

              {/* Start Node */}
              <div className="flex flex-col gap-1">
                <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  Start Node
                </label>
                <input
                  type="text"
                  name="start_node"
                  value={form.start_node}
                  onChange={handleChange}
                  placeholder="e.g. n5"
                  autoComplete="off"
                  className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-sky-500 focus:border-sky-500 transition"
                />
                <p className="text-[10px] text-slate-500 mt-0.5">
                  Valid: n1 – n14
                </p>
              </div>

              {/* Error */}
              {error && (
                <div className="bg-red-950/60 border border-red-800 rounded-lg px-3 py-2 text-xs text-red-400">
                  {error}
                </div>
              )}

              {/* Submit */}
              <button
                type="submit"
                disabled={loading}
                className="mt-1 w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-sm tracking-wide py-2.5 rounded-lg border border-indigo-400 shadow-lg shadow-indigo-600/20 transition"
              >
                {loading ? (
                  <span className="flex items-center justify-center gap-2">
                    <span className="w-3 h-3 rounded-full border-2 border-white/30 border-t-white animate-spin" />
                    Connecting…
                  </span>
                ) : (
                  "ENTER ENVIRONMENT"
                )}
              </button>
            </form>
          )}
        </div>

        <p className="text-center text-slate-600 text-xs mt-4">
          Each registration creates a unique AMR in the shared simulation.
        </p>
      </div>
    </div>
  );
}
