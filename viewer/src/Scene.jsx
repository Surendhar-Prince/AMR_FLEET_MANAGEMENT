import { Html, OrbitControls } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import { DoubleSide, Shape } from "three";
import { useSimulationState } from "./useSimulationState";

// A flat 2D arrow centered at the origin
function createArrowShape(length, width) {
  const headLength = length * 0.5;
  const shaftWidth = width * 0.4;
  const shaftBackX = -length / 2;
  const headBackX = length / 2 - headLength;
  const tipX = length / 2;

  const shape = new Shape();
  shape.moveTo(shaftBackX, -shaftWidth / 2);
  shape.lineTo(headBackX, -shaftWidth / 2);
  shape.lineTo(headBackX, -width / 2);
  shape.lineTo(tipX, 0);
  shape.lineTo(headBackX, width / 2);
  shape.lineTo(headBackX, shaftWidth / 2);
  shape.lineTo(shaftBackX, shaftWidth / 2);
  shape.closePath();
  return shape;
}

function FlatArrow({ shape, color }) {
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]}>
      <shapeGeometry args={[shape]} />
      <meshBasicMaterial color={color} side={DoubleSide} />
    </mesh>
  );
}

const EDGE_ARROW_SHAPE = createArrowShape(0.4, 0.22);

function colorForId(id) {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  }
  return `hsl(${hash % 360}, 75%, 55%)`;
}

const labelStyle = {
  padding: "1px 5px",
  borderRadius: 3,
  background: "rgba(15, 23, 42, 0.85)",
  color: "#f8fafc",
  fontSize: 11,
  fontFamily: "monospace",
  fontWeight: "bold",
  whiteSpace: "nowrap",
  pointerEvents: "none",
  border: "1px solid rgba(100, 116, 139, 0.4)",
};

// Visual Path Trajectory Line for an AMR (Vibrant Green for Active Transit, Amber for Detour/Yielding)
function AmrTrajectoryLine({ amr, nodeById }) {
  if (!amr.path || amr.path.length === 0) return null;

  const points = useMemo(() => {
    const pts = [amr.position.x, 0.045, amr.position.y];
    for (const nodeId of amr.path) {
      const node = nodeById.get(nodeId);
      if (node) {
        pts.push(node.x, 0.045, node.y);
      }
    }
    return new Float32Array(pts);
  }, [amr.position.x, amr.position.y, amr.path, nodeById]);

  // Active path is vibrant green (#10b981), Detour/Yielding is amber (#f59e0b)
  const lineColor = amr.state_label === "YIELDING" ? "#f59e0b" : "#10b981";

  return (
    <line>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[points, 3]} />
      </bufferGeometry>
      <lineBasicMaterial color={lineColor} linewidth={3} transparent opacity={0.9} />
    </line>
  );
}

function MapGeometry({ map, amrs, selectedNode, onSelectNode, theme = "light" }) {
  const nodeById = useMemo(
    () => new Map(map.nodes.map((n) => [n.id, n])),
    [map.nodes]
  );

  // Set of nodes currently occupied by any AMR
  const occupiedNodes = useMemo(() => {
    const s = new Set();
    amrs.forEach((a) => {
      if (a.path.length === 0 && a.current_node) s.add(a.current_node);
    });
    return s;
  }, [amrs]);

  // Set of nodes occupied by a disabled / failed AMR (GHOST / QUARANTINED NODES)
  const failedNodes = useMemo(() => {
    const s = new Set();
    amrs.forEach((a) => {
      if (a.state_label === "FAILED" && a.current_node) s.add(a.current_node);
    });
    return s;
  }, [amrs]);

  // Set of nodes planned/reserved in any moving AMR's path
  const reservedNodes = useMemo(() => {
    const s = new Set();
    amrs.forEach((a) => {
      a.path.forEach((n) => s.add(n));
    });
    return s;
  }, [amrs]);

  const isLight = theme === "light";

  return (
    <group>
      {/* Station Nodes */}
      {map.nodes.map((node) => {
        const isFailed = failedNodes.has(node.id);
        const isOccupied = occupiedNodes.has(node.id);
        const isReserved = reservedNodes.has(node.id);
        const isSelected = selectedNode === node.id;
        const isChargingDock = node.id === "n14";

        // Dynamic node color coding
        const circleColor = isFailed
          ? "#ef4444"
          : isChargingDock
          ? "#10b981"
          : isReserved
          ? "#0284c7"
          : isOccupied
          ? "#f97316"
          : isLight
          ? "#3b82f6"
          : "#64748b";

        return (
          <group
            key={node.id}
            position={[node.x, 0.03, node.y]}
            onClick={(e) => {
              e.stopPropagation();
              if (onSelectNode) onSelectNode(node.id);
            }}
          >
            {/* Elevated 3D Station Dock */}
            <mesh rotation={[-Math.PI / 2, 0, 0]}>
              <circleGeometry args={[0.26, 32]} />
              <meshStandardMaterial
                color={circleColor}
                roughness={0.2}
                metalness={0.1}
                side={DoubleSide}
              />
            </mesh>

            {/* Outer Ring Border */}
            <mesh rotation={[-Math.PI / 2, 0, 0]}>
              <ringGeometry args={[0.26, 0.32, 32]} />
              <meshBasicMaterial
                color={isSelected ? "#ec4899" : isChargingDock ? "#059669" : isLight ? "#93c5fd" : "#475569"}
                side={DoubleSide}
              />
            </mesh>

            {/* Glowing RED Hazard Ring for Failed / Ghost Station */}
            {isFailed && (
              <mesh rotation={[-Math.PI / 2, 0, 0]}>
                <ringGeometry args={[0.34, 0.44, 32]} />
                <meshBasicMaterial color="#ef4444" transparent opacity={0.85} side={DoubleSide} />
              </mesh>
            )}

            {/* Selected Station Pulse Ring */}
            {isSelected && (
              <mesh rotation={[-Math.PI / 2, 0, 0]}>
                <ringGeometry args={[0.36, 0.48, 32]} />
                <meshBasicMaterial color="#ec4899" transparent opacity={0.9} side={DoubleSide} />
              </mesh>
            )}

            <Html position={[0, 0.32, 0]} center>
              <div
                style={{
                  ...labelStyle,
                  background: isLight ? "rgba(255, 255, 255, 0.95)" : "rgba(15, 23, 42, 0.85)",
                  color: isLight ? "#0f172a" : "#f8fafc",
                  borderColor: isSelected
                    ? "#ec4899"
                    : isChargingDock
                    ? "#10b981"
                    : isLight
                    ? "#cbd5e1"
                    : "rgba(100, 116, 139, 0.4)",
                  boxShadow: isLight ? "0 2px 8px rgba(0,0,0,0.12)" : "none",
                  cursor: "pointer",
                }}
              >
                {isChargingDock ? `⚡ ${node.id} (Bay)` : node.id}
              </div>
            </Html>
          </group>
        );
      })}

      {/* Directed Graph Edges with Dynamic Color Coding */}
      {map.edges.map((edge) => {
        const from = nodeById.get(edge.from);
        const to = nodeById.get(edge.to);
        if (!from || !to) return null;

        const isGhostCorridor = failedNodes.has(edge.from) || failedNodes.has(edge.to);

        const edgeColor = isGhostCorridor
          ? "#ef4444"
          : isLight
          ? "#94a3b8"
          : "#475569";
        const arrowColor = isGhostCorridor
          ? "#ef4444"
          : isLight
          ? "#64748b"
          : "#94a3b8";

        const midX = (from.x + to.x) / 2;
        const midY = (from.y + to.y) / 2;
        const angle = Math.atan2(to.y - from.y, to.x - from.x);
        const pts = new Float32Array([from.x, 0.02, from.y, to.x, 0.02, to.y]);

        return (
          <group key={`${edge.from}->${edge.to}`}>
            <line>
              <bufferGeometry>
                <bufferAttribute attach="attributes-position" args={[pts, 3]} />
              </bufferGeometry>
              <lineBasicMaterial
                color={edgeColor}
                linewidth={isGhostCorridor ? 3 : 2}
                transparent
                opacity={isGhostCorridor ? 0.95 : 0.8}
              />
            </line>
            <group position={[midX, 0.025, midY]} rotation={[0, -angle, 0]}>
              <FlatArrow shape={EDGE_ARROW_SHAPE} color={arrowColor} />
            </group>
          </group>
        );
      })}

      {/* Real-Time Colored Path Trajectory Lines */}
      {amrs.map((amr) => (
        <AmrTrajectoryLine key={amr.id} amr={amr} nodeById={nodeById} />
      ))}
    </group>
  );
}

function AmrModel({ amr, map, theme = "light" }) {
  const wheelRadius = Math.min(map.amr_width, map.amr_length) * 0.15;
  const wheelThickness = wheelRadius * 0.6;
  const chassisHeight = wheelRadius * 1.6;
  const halfLength = map.amr_length / 2;
  const halfWidth = map.amr_width / 2;
  const wheelInsetL = halfLength * 0.65;
  const wheelInsetW = halfWidth * 0.9;

  const noseArrowShape = useMemo(
    () => createArrowShape(map.amr_length * 0.6, map.amr_width * 0.35),
    [map.amr_length, map.amr_width]
  );
  const identityColor = useMemo(() => colorForId(amr.id), [amr.id]);
  const chassisColor =
    amr.is_remote
      ? "#0284c7"
      : amr.colliding || amr.state_label === "FAILED"
      ? "#ef4444"
      : amr.state_label === "YIELDING"
      ? "#f59e0b"
      : amr.state_label === "CHARGING"
      ? "#10b981"
      : identityColor;

  const wheelRefs = useRef([]);
  const spinRef = useRef(0);
  const isMoving = amr.path && amr.path.length > 0;

  useFrame((_, delta) => {
    const speed = isMoving ? map.amr_speed : 0;
    spinRef.current += (speed / wheelRadius) * delta;
    for (const wheel of wheelRefs.current) {
      if (wheel) wheel.rotation.z = spinRef.current;
    }
  });

  const wheelPositions = [
    [wheelInsetL, 0, wheelInsetW],
    [wheelInsetL, 0, -wheelInsetW],
    [-wheelInsetL, 0, wheelInsetW],
    [-wheelInsetL, 0, -wheelInsetW],
  ];

  const isLight = theme === "light";

  return (
    <group
      position={[amr.position.x, wheelRadius, amr.position.y]}
      rotation={[0, -amr.heading, 0]}
    >
      <mesh position={[0, chassisHeight / 2, 0]}>
        <boxGeometry args={[map.amr_length, chassisHeight, map.amr_width]} />
        <meshStandardMaterial
          color={chassisColor}
          wireframe={Boolean(amr.is_remote)}
          transparent={Boolean(amr.is_remote)}
          opacity={amr.is_remote ? 0.75 : 1.0}
          roughness={0.3}
          metalness={0.2}
        />
      </mesh>
      <group position={[0, chassisHeight + 0.01, 0]}>
        <FlatArrow shape={noseArrowShape} color={amr.is_remote ? "#38bdf8" : "#fbbf24"} />
      </group>
      <Html position={[0, chassisHeight + 0.45, 0]} center>
        <div
          style={{
            ...labelStyle,
            background: isLight ? "rgba(255, 255, 255, 0.95)" : "rgba(15, 23, 42, 0.85)",
            color: amr.is_remote ? "#0284c7" : isLight ? "#0f172a" : "#f8fafc",
            borderColor: amr.is_remote ? "#0284c7" : isLight ? "#cbd5e1" : "rgba(100, 116, 139, 0.4)",
            boxShadow: isLight ? "0 2px 8px rgba(0,0,0,0.15)" : "none",
          }}
        >
          {amr.is_remote ? `📡 ${amr.id} (Remote)` : amr.id}
        </div>
      </Html>
      {wheelPositions.map((pos, i) => (
        <group
          key={i}
          position={pos}
          ref={(el) => {
            wheelRefs.current[i] = el;
          }}
        >
          <group rotation={[Math.PI / 2, 0, 0]}>
            <mesh>
              <cylinderGeometry args={[wheelRadius, wheelRadius, wheelThickness, 16]} />
              <meshStandardMaterial color="#1e293b" />
            </mesh>
            <mesh position={[0, wheelThickness / 2 + 0.005, 0]}>
              <boxGeometry args={[wheelRadius * 1.7, 0.01, wheelRadius * 0.16]} />
              <meshBasicMaterial color="#ffffff" />
            </mesh>
            <mesh position={[0, -(wheelThickness / 2 + 0.005), 0]}>
              <boxGeometry args={[wheelRadius * 1.7, 0.01, wheelRadius * 0.16]} />
              <meshBasicMaterial color="#ffffff" />
            </mesh>
          </group>
        </group>
      ))}
    </group>
  );
}

function AmrModels({ map, amrs, theme }) {
  const simAmrs = amrs || useSimulationState();

  return (
    <group>
      {simAmrs.map((amr) => (
        <AmrModel key={amr.id} amr={amr} map={map} theme={theme} />
      ))}
    </group>
  );
}

export function Scene({ amrs, selectedNode, onSelectNode, theme = "light" }) {
  const [map, setMap] = useState(null);
  const simAmrs = amrs || useSimulationState();

  useEffect(() => {
    fetch("/api/map")
      .then((res) => res.json())
      .then(setMap);
  }, []);

  if (!map) return null;

  const center = map.nodes.reduce(
    (acc, n) => ({ x: acc.x + n.x / map.nodes.length, y: acc.y + n.y / map.nodes.length }),
    { x: 0, y: 0 }
  );

  const isLight = theme === "light";

  return (
    <Canvas
      camera={{ position: [center.x, 14, center.y + 10], fov: 48 }}
      style={{ background: isLight ? "#f8fafc" : "#0b0f17" }}
    >
      <ambientLight intensity={isLight ? 1.1 : 0.8} />
      <directionalLight position={[5, 14, 5]} intensity={isLight ? 0.9 : 0.7} />

      {/* Warehouse Floor & Floor Grid */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[center.x, 0, center.y]}>
        <planeGeometry args={[40, 40]} />
        <meshStandardMaterial
          color={isLight ? "#f1f5f9" : "#080c14"}
          roughness={0.4}
          metalness={0.1}
        />
      </mesh>
      <gridHelper
        args={[40, 40, isLight ? "#94a3b8" : "#334155", isLight ? "#e2e8f0" : "#1e293b"]}
        position={[center.x, 0.01, center.y]}
      />

      <MapGeometry
        map={map}
        amrs={simAmrs}
        selectedNode={selectedNode}
        onSelectNode={onSelectNode}
        theme={theme}
      />
      <AmrModels map={map} amrs={simAmrs} theme={theme} />
      <OrbitControls target={[center.x, 0, center.y]} />
    </Canvas>
  );
}
