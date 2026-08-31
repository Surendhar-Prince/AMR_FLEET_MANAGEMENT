import { useEffect, useRef, useState } from "react";

export function useSimulationState() {
  const [amrs, setAmrs] = useState([]);
  const socketRef = useRef(null);

  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${window.location.host}/ws`);
    socketRef.current = socket;

    socket.onmessage = (event) => {
      setAmrs(JSON.parse(event.data));
    };

    return () => socket.close();
  }, []);

  return amrs;
}
