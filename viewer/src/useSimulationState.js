import { useEffect, useRef, useState } from "react";
import { apiUrl } from "./api";

export function useSimulationState() {
  const [amrs, setAmrs] = useState([]);
  const socketRef = useRef(null);

  useEffect(() => {
    // 1. Immediate REST fetch on page load so AMRs never disappear on refresh
    fetch(apiUrl("/api/amrs"))
      .then((res) => (res.ok ? res.json() : []))
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          setAmrs(data);
        }
      })
      .catch(() => { });

    // 2. High-speed 20 Hz WebSocket Stream
    let socket;
    try {
      const socketUrl = import.meta.env.VITE_API_URL
        ? apiUrl("/ws").replace(/^http/, "ws")
        : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.port === "3000" ? `${window.location.hostname}:8000` : window.location.host
        }/ws`;

      socket = new WebSocket(socketUrl);
      socketRef.current = socket;

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (Array.isArray(data)) {
            setAmrs(data);
          }
        } catch {
          // ignore parse errors
        }
      };
    } catch (e) {
      console.error("WebSocket connection error:", e);
    }

    // 3. Fallback polling loop (200ms) to ensure continuous synchronization
    const pollInterval = setInterval(() => {
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        fetch(apiUrl("/api/amrs"))
          .then((res) => (res.ok ? res.json() : []))
          .then((data) => {
            if (Array.isArray(data)) {
              setAmrs(data);
            }
          })
          .catch(() => { });
      }
    }, 200);

    return () => {
      if (socket) socket.close();
      clearInterval(pollInterval);
    };
  }, []);

  return amrs;
}
