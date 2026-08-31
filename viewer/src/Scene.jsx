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

function MapGeometry({ map, amrs }) {
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

  return (
    <group>
      {/* Station Nodes */}
      {map.nodes.map((node) => {
        const isFailed = failedNodes.has(node.id);
        const isOccupied = occupiedNodes.has(node.id);
        const isReserved = reservedNodes.has(node.id);

        // Failed node is Red, Active path node is Green, Occupied is Orange, Normal is Grey
        const circleColor = isFailed
          ? "#ef4444"
          : isReserved
          ? "#10b981"
          : isOccupied
          ? "#f97316"
          : "#64748b";

        return (
          <group key={node.id} position={[node.x, 0.03, node.y]}>
            <mesh rotation={[-Math.PI / 2, 0, 0]}>
              <circleGeometry args={[0.18, 24]} />
              <meshBasicMaterial color={circleColor} side={DoubleSide} />
            </mesh>

            {/* Glowing RED Hazard Ring for Failed / Ghost Station */}
            {isFailed && (
              <mesh rotation={[-Math.PI / 2, 0, 0]}>
                <ringGeometry args={[0.22, 0.32, 24]} />
                <meshBasicMaterial color="#ef4444" transparent opacity={0.85} side={DoubleSide} />
              </mesh>
            )}

            {/* Glowing GREEN Active Trajectory Ring for Moving AMRs */}
            {isReserved && !isFailed && (
              <mesh rotation={[-Math.PI / 2, 0, 0]}>
                <ringGeometry args={[0.22, 0.28, 24]} />
                <meshBasicMaterial color="#10b981" transparent opacity={0.7} side={DoubleSide} />
              </mesh>
            )}

            <Html position={[0, 0.22, 0]} center>
              <div
                style={{
                  ...labelStyle,
                  borderColor: isFailed
                    ? "#ef4444"
                    : isReserved
                    ? "#10b981"
                    : "rgba(100, 116, 139, 0.4)",
                }}
              >
                {node.id}
              </div>
            </Html>
          </group>
        );
      })}

      {/* Grid Network Edges (Highlighted RED if connected to a Failed Ghost Node) */}
      {map.edges.map((edge, i) => {
        const from = nodeById.get(edge.from);
        const to = nodeById.get(edge.to);
        if (!from || !to) return null;

        const isGhostCorridor = failedNodes.has(edge.from) || failedNodes.has(edge.to);
        const edgeColor = isGhostCorridor ? "#ef4444" : "#334155";
        const arrowColor = isGhostCorridor ? "#ef4444" : "#475569";

        const points = new Float32Array([from.x, 0.02, from.y, to.x, 0.02, to.y]);
        const angle = Math.atan2(to.y - from.y, to.x - from.x);
        const midX = from.x + (to.x - from.x) * 0.6;
        const midY = from.y + (to.y - from.y) * 0.6;

        return (
          <group key={i}>
            <line>
              <bufferGeometry>
                <bufferAttribute attach="attributes-position" args={[points, 3]} />
              </bufferGeometry>
              <lineBasicMaterial
                color={edgeColor}
                linewidth={isGhostCorridor ? 3 : 1}
                transparent
                opacity={isGhostCorridor ? 0.95 : 0.6}
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

function AmrModel({ amr, map }) {
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
    amr.colliding || amr.state_label === "FAILED"
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

  return (
    <group
      position={[amr.position.x, wheelRadius, amr.position.y]}
      rotation={[0, -amr.heading, 0]}
    >
      <mesh position={[0, chassisHeight / 2, 0]}>
        <boxGeometry args={[map.amr_length, chassisHeight, map.amr_width]} />
        <meshStandardMaterial color={chassisColor} />
      </mesh>
      <group position={[0, chassisHeight + 0.01, 0]}>
        <FlatArrow shape={noseArrowShape} color="#fbbf24" />
      </group>
      <Html position={[0, chassisHeight + 0.45, 0]} center>
        <div style={labelStyle}>{amr.id}</div>
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

function AmrModels({ map, amrs }) {
  const simAmrs = amrs || useSimulationState();

  return (
    <group>
      {simAmrs.map((amr) => (
        <AmrModel key={amr.id} amr={amr} map={map} />
      ))}
    </group>
  );
}

export function Scene({ amrs }) {
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

  return (
    <Canvas camera={{ position: [center.x, 14, center.y + 10], fov: 48 }}>
      <ambientLight intensity={0.8} />
      <directionalLight position={[5, 12, 5]} intensity={0.7} />
      <MapGeometry map={map} amrs={simAmrs} />
      <AmrModels map={map} amrs={simAmrs} />
      <OrbitControls target={[center.x, 0, center.y]} />
    </Canvas>
  );
}
