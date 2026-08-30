import { useEffect, useRef, useState } from "react";
import type { AmrState } from "./types";

export function useSimulationState(): AmrState[] {
  const [amrs, setAmrs] = useState<AmrState[]>([]);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${window.location.host}/ws`);
    socketRef.current = socket;

    socket.onmessage = (event) => {
      setAmrs(JSON.parse(event.data) as AmrState[]);
    };

    return () => socket.close();
  }, []);

  return amrs;
}
