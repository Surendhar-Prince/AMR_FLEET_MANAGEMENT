import { useEffect, useRef, useState } from "react";
import { apiUrl } from "./api";

export function useSimulationState() {
  const [amrs, setAmrs] = useState([]);
  const socketRef = useRef(null);

  useEffect(() => {
    const socketUrl = import.meta.env.VITE_API_URL
      ? apiUrl("/ws").replace(/^http/, "ws")
      : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws`;
    const socket = new WebSocket(socketUrl);
    socketRef.current = socket;

    socket.onmessage = (event) => {
      setAmrs(JSON.parse(event.data));
    };

    return () => socket.close();
  }, []);

  return amrs;
}
