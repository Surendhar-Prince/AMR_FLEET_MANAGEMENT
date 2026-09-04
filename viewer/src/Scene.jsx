import { Html, OrbitControls, Text } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import React, { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { apiUrl } from "./api";
import { useSimulationState } from "./useSimulationState";

// A flat 2D arrow centered at the origin
function createArrowShape(length, width) {
  const headLength = length * 0.5;
  const shaftWidth = width * 0.4;
  const shaftBackX = -length / 2;
  const headBackX = length / 2 - headLength;
  const tipX = length / 2;

  const shape = new THREE.Shape();
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
      <meshBasicMaterial color={color} side={THREE.DoubleSide} />
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

// Clean 3D Floor Track Ribbon for paths and corridors
function RibbonTrack({ from, to, color = "#cbd5e1", width = 0.09, height = 0.015, opacity = 0.85 }) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return null;
  const midX = (from.x + to.x) / 2;
  const midY = (from.y + to.y) / 2;
  const angle = Math.atan2(dy, dx);

  return (
    <mesh position={[midX, height, midY]} rotation={[-Math.PI / 2, 0, -angle]}>
      <planeGeometry args={[len, width]} />
      <meshBasicMaterial color={color} transparent opacity={opacity} side={THREE.DoubleSide} depthWrite={false} />
    </mesh>
  );
}

// Visual Path Trajectory Ribbons for an AMR
function AmrTrajectoryLine({ amr, nodeById }) {
  if (!amr.path || amr.path.length === 0) return null;

  const color = amr.state_label === "YIELDING" ? "#f59e0b" : "#10b981";
  const segments = [];
  let prev = { x: amr.position.x, y: amr.position.y };

  for (const nodeId of amr.path) {
    const node = nodeById.get(nodeId);
    if (node) {
      segments.push({ from: prev, to: { x: node.x, y: node.y } });
      prev = { x: node.x, y: node.y };
    }
  }

  return (
    <group>
      {segments.map((seg, idx) => (
        <RibbonTrack
          key={`traj-${amr.id}-${idx}`}
          from={seg.from}
          to={seg.to}
          color={color}
          width={0.14}
          height={0.038}
          opacity={0.92}
        />
      ))}
    </group>
  );
}

function MapGeometry({ map, amrs, selectedNode, onSelectNode }) {
  const nodeById = useMemo(
    () => new Map(map.nodes.map((n) => [n.id, n])),
    [map.nodes]
  );

  const occupiedNodes = useMemo(() => {
    const s = new Set();
    amrs.forEach((a) => {
      if (a.path.length === 0 && a.current_node) s.add(a.current_node);
    });
    return s;
  }, [amrs]);

  const failedNodes = useMemo(() => {
    const s = new Set();
    amrs.forEach((a) => {
      if (a.state_label === "FAILED" && a.current_node) s.add(a.current_node);
    });
    return s;
  }, [amrs]);

  const reservedNodes = useMemo(() => {
    const s = new Set();
    amrs.forEach((a) => {
      if (a.path.length > 0) s.add(a.path[0]);
    });
    return s;
  }, [amrs]);

  return (
    <group>
      {/* 1. Dynamic Active Trajectory Lines for moving AMRs */}
      {amrs.map((amr) => (
        <AmrTrajectoryLine key={`traj-${amr.id}`} amr={amr} nodeById={nodeById} />
      ))}

      {/* 2. Warehouse Station Nodes */}
      {map.nodes.map((node) => {
        const isFailed = failedNodes.has(node.id);
        const isOccupied = occupiedNodes.has(node.id);
        const isReserved = reservedNodes.has(node.id);
        const isSelected = selectedNode === node.id;
        const isChargingDock =
          node.type === "charging" ||
          node.id.toLowerCase().includes("charge") ||
          node.id === "n5" ||
          node.id === "n10" ||
          node.id === "n14";

        const circleColor = isFailed
          ? "#ef4444"
          : isChargingDock
            ? "#10b981"
            : isReserved
              ? "#0284c7"
              : isOccupied
                ? "#f97316"
                : "#3b82f6";

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
              <circleGeometry args={[0.32, 32]} />
              <meshStandardMaterial
                color={circleColor}
                roughness={0.2}
                metalness={0.2}
                side={THREE.DoubleSide}
              />
            </mesh>

            {/* Outer Ring Border */}
            <mesh rotation={[-Math.PI / 2, 0, 0]}>
              <ringGeometry args={[0.32, 0.4, 32]} />
              <meshBasicMaterial
                color={isSelected ? "#ec4899" : isChargingDock ? "#059669" : "#60a5fa"}
                side={THREE.DoubleSide}
              />
            </mesh>

            {/* Glowing RED Hazard Ring for Failed / Ghost Station */}
            {isFailed && (
              <mesh rotation={[-Math.PI / 2, 0, 0]}>
                <ringGeometry args={[0.42, 0.54, 32]} />
                <meshBasicMaterial color="#ef4444" transparent opacity={0.85} side={THREE.DoubleSide} />
              </mesh>
            )}

            {/* Selected Station Pulse Ring */}
            {isSelected && (
              <mesh rotation={[-Math.PI / 2, 0, 0]}>
                <ringGeometry args={[0.44, 0.58, 32]} />
                <meshBasicMaterial color="#ec4899" transparent opacity={0.9} side={THREE.DoubleSide} />
              </mesh>
            )}

            {/* Crisp 3D Text Label Directly Anchored to Station Pad */}
            <Text
              position={[0, 0.02, 0]}
              rotation={[-Math.PI / 2, 0, 0]}
              fontSize={0.2}
              color="#ffffff"
              anchorX="center"
              anchorY="middle"
              fontWeight="bold"
            >
              {isChargingDock ? `⚡ ${node.id}` : node.id}
            </Text>
          </group>
        );
      })}

      {/* 3. Directed Graph Track Corridors */}
      {map.edges.map((edge, i) => {
        const fromId = edge.from || edge.source;
        const toId = edge.to || edge.target;
        const from = nodeById.get(fromId);
        const to = nodeById.get(toId);
        if (!from || !to) return null;

        const isGhostCorridor = failedNodes.has(fromId) || failedNodes.has(toId);
        const trackColor = isGhostCorridor
          ? "#ef4444"
          : "#94a3b8";

        const dx = to.x - from.x;
        const dy = to.y - from.y;
        const length = Math.hypot(dx, dy);
        const midX = (from.x + to.x) / 2;
        const midY = (from.y + to.y) / 2;
        const angle = Math.atan2(dy, dx);

        const arrowOffset = 0.35;
        const ax = from.x + dx * arrowOffset;
        const ay = from.y + dy * arrowOffset;

        return (
          <group key={i}>
            {/* Bold Corridor Track Ribbon */}
            <RibbonTrack
              from={from}
              to={to}
              color={trackColor}
              width={isGhostCorridor ? 0.12 : 0.07}
              height={0.015}
              opacity={isGhostCorridor ? 0.95 : 0.75}
            />
            {/* Directional Chevron Arrow */}
            <group position={[ax, 0.03, ay]} rotation={[0, -angle, 0]}>
              <FlatArrow shape={EDGE_ARROW_SHAPE} color={isGhostCorridor ? "#ef4444" : "#64748b"} />
            </group>
          </group>
        );
      })}
    </group>
  );
}

function AmrModel({ amr, map }) {
  const amrWidth = map?.amr_width || 0.8;
  const amrLength = map?.amr_length || 1.2;
  const amrSpeed = map?.amr_speed || 1.5;

  const wheelRadius = Math.min(amrWidth, amrLength) * 0.18;
  const wheelThickness = wheelRadius * 0.55;
  const chassisHeight = wheelRadius * 1.6;
  const halfLength = amrLength / 2;
  const halfWidth = amrWidth / 2;
  const wheelInsetL = halfLength * 0.65;
  const wheelInsetW = halfWidth * 0.88;

  const identityColor = useMemo(() => colorForId(amr.id), [amr.id]);
  const statusColor =
    amr.is_remote
      ? "#0284c7"
      : amr.colliding || amr.state_label === "FAILED"
        ? "#ef4444"
        : amr.state_label === "YIELDING"
          ? "#f59e0b"
          : amr.state_label === "CHARGING"
            ? "#06b6d4"
            : "#10b981";

  const wheelRefs = useRef([]);
  const lidarRef = useRef(null);
  const liftRef = useRef(null);
  const spinRef = useRef(0);
  const liftHeightRef = useRef(0.02);

  const isMoving = amr.path && amr.path.length > 0;
  const hasPayload = Boolean(amr.active_task && amr.state_label !== "IDLE");

  useFrame((_, delta) => {
    // 1. Wheel Kinetic Rotation
    const speed = isMoving ? amrSpeed : 0;
    spinRef.current += (speed / wheelRadius) * delta;
    for (const wheel of wheelRefs.current) {
      if (wheel) wheel.rotation.z = spinRef.current;
    }

    // 2. Spinning 360 LiDAR Optical Laser
    if (lidarRef.current) {
      lidarRef.current.rotation.y += delta * 15;
    }

    // 3. Smooth Mechanical Scissor Lifter Deck Animation (Lifting & Lowering)
    const targetLift = hasPayload ? 0.22 : 0.02;
    liftHeightRef.current = THREE.MathUtils.lerp(liftHeightRef.current, targetLift, Math.min(1.0, delta * 5));
    if (liftRef.current) {
      liftRef.current.position.y = chassisHeight + liftHeightRef.current;
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
      {/* 1. Status Halo Underglow on Floor */}
      <mesh position={[0, -wheelRadius + 0.02, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[0.25, 0.55, 32]} />
        <meshBasicMaterial color={statusColor} transparent opacity={0.65} side={THREE.DoubleSide} />
      </mesh>

      {/* 2. Heavy-Duty Industrial Base Plate (Matte Gunmetal) */}
      <mesh position={[0, chassisHeight * 0.45, 0]}>
        <boxGeometry args={[amrLength * 0.96, chassisHeight * 0.7, amrWidth * 0.94]} />
        <meshStandardMaterial color="#1e293b" roughness={0.3} metalness={0.7} />
      </mesh>

      {/* 3. Safety Armor Shell with Custom Identity Color */}
      <mesh position={[0, chassisHeight * 0.82, 0]}>
        <boxGeometry args={[amrLength * 0.88, chassisHeight * 0.45, amrWidth * 0.86]} />
        <meshStandardMaterial
          color={amr.is_remote ? "#0284c7" : identityColor}
          roughness={0.25}
          metalness={0.4}
        />
      </mesh>

      {/* 4. Safety Bumper Corner Guards with Hazard Yellow Stripes */}
      <mesh position={[amrLength * 0.47, chassisHeight * 0.4, 0]}>
        <boxGeometry args={[0.08, chassisHeight * 0.55, amrWidth * 0.9]} />
        <meshStandardMaterial color="#eab308" roughness={0.3} metalness={0.2} />
      </mesh>
      <mesh position={[-amrLength * 0.47, chassisHeight * 0.4, 0]}>
        <boxGeometry args={[0.08, chassisHeight * 0.55, amrWidth * 0.9]} />
        <meshStandardMaterial color="#eab308" roughness={0.3} metalness={0.2} />
      </mesh>

      {/* 5. Front LED Headlight Beams */}
      <mesh position={[amrLength * 0.49, chassisHeight * 0.6, amrWidth * 0.28]}>
        <boxGeometry args={[0.02, 0.07, 0.12]} />
        <meshBasicMaterial color="#e0f2fe" />
      </mesh>
      <mesh position={[amrLength * 0.49, chassisHeight * 0.6, -amrWidth * 0.28]}>
        <boxGeometry args={[0.02, 0.07, 0.12]} />
        <meshBasicMaterial color="#e0f2fe" />
      </mesh>

      {/* 6. Rear Red Tail Status LEDs */}
      <mesh position={[-amrLength * 0.49, chassisHeight * 0.6, amrWidth * 0.28]}>
        <boxGeometry args={[0.02, 0.07, 0.12]} />
        <meshBasicMaterial color={statusColor} />
      </mesh>
      <mesh position={[-amrLength * 0.49, chassisHeight * 0.6, -amrWidth * 0.28]}>
        <boxGeometry args={[0.02, 0.07, 0.12]} />
        <meshBasicMaterial color={statusColor} />
      </mesh>

      {/* 7. Front 360° Safety LiDAR Turret */}
      <group position={[amrLength * 0.35, chassisHeight + 0.04, 0]}>
        <mesh>
          <cylinderGeometry args={[0.09, 0.11, 0.06, 24]} />
          <meshStandardMaterial color="#0f172a" metalness={0.8} />
        </mesh>
        <group ref={lidarRef} position={[0, 0.045, 0]}>
          <mesh>
            <cylinderGeometry args={[0.075, 0.075, 0.05, 24]} />
            <meshStandardMaterial color="#1e293b" metalness={0.9} roughness={0.1} />
          </mesh>
          <mesh position={[0.065, 0, 0]}>
            <boxGeometry args={[0.025, 0.03, 0.04]} />
            <meshBasicMaterial color="#38bdf8" />
          </mesh>
        </group>
      </group>

      {/* 8. ACTIVE MECHANICAL PALLET / SCISSOR LIFTER DECK */}
      <group ref={liftRef} position={[0, chassisHeight + 0.02, 0]}>
        {/* Top Hydraulic Steel Lifting Platform */}
        <mesh position={[0, 0, 0]}>
          <boxGeometry args={[amrLength * 0.78, 0.04, amrWidth * 0.8]} />
          <meshStandardMaterial color="#334155" metalness={0.8} roughness={0.3} />
        </mesh>

        {/* Rubber Anti-Slip Grip Top */}
        <mesh position={[0, 0.022, 0]}>
          <boxGeometry args={[amrLength * 0.72, 0.008, amrWidth * 0.74]} />
          <meshStandardMaterial color="#0f172a" roughness={0.9} />
        </mesh>

        {/* Scissor Lift X-Linkages */}
        <mesh position={[amrLength * 0.2, -0.06, amrWidth * 0.35]} rotation={[0, 0, 0.45]}>
          <boxGeometry args={[0.18, 0.02, 0.015]} />
          <meshStandardMaterial color="#94a3b8" metalness={0.9} />
        </mesh>
        <mesh position={[-amrLength * 0.2, -0.06, amrWidth * 0.35]} rotation={[0, 0, -0.45]}>
          <boxGeometry args={[0.18, 0.02, 0.015]} />
          <meshStandardMaterial color="#94a3b8" metalness={0.9} />
        </mesh>
        <mesh position={[amrLength * 0.2, -0.06, -amrWidth * 0.35]} rotation={[0, 0, 0.45]}>
          <boxGeometry args={[0.18, 0.02, 0.015]} />
          <meshStandardMaterial color="#94a3b8" metalness={0.9} />
        </mesh>
        <mesh position={[-amrLength * 0.2, -0.06, -amrWidth * 0.35]} rotation={[0, 0, -0.45]}>
          <boxGeometry args={[0.18, 0.02, 0.015]} />
          <meshStandardMaterial color="#94a3b8" metalness={0.9} />
        </mesh>

        {/* Real Industrial Wooden Pallet & Package Box */}
        {hasPayload && (
          <group position={[0, 0.03, 0]}>
            {/* Wooden Pallet Base */}
            <mesh position={[0, 0.04, 0]}>
              <boxGeometry args={[amrLength * 0.65, 0.06, amrWidth * 0.68]} />
              <meshStandardMaterial color="#b45309" roughness={0.8} />
            </mesh>
            {/* Wooden Grooves */}
            <mesh position={[0, 0.015, amrWidth * 0.26]}>
              <boxGeometry args={[amrLength * 0.64, 0.03, 0.06]} />
              <meshStandardMaterial color="#78350f" roughness={0.9} />
            </mesh>
            <mesh position={[0, 0.015, -amrWidth * 0.26]}>
              <boxGeometry args={[amrLength * 0.64, 0.03, 0.06]} />
              <meshStandardMaterial color="#78350f" roughness={0.9} />
            </mesh>
            {/* Cardboard Cargo Box */}
            <mesh position={[0, 0.22, 0]}>
              <boxGeometry args={[amrLength * 0.52, 0.3, amrWidth * 0.54]} />
              <meshStandardMaterial color="#d97706" roughness={0.7} />
            </mesh>
            {/* Yellow Strapping Tape */}
            <mesh position={[0, 0.22, 0]}>
              <boxGeometry args={[amrLength * 0.53, 0.305, 0.08]} />
              <meshStandardMaterial color="#fde047" roughness={0.4} />
            </mesh>
          </group>
        )}
      </group>

      {/* Floating Status & Telemetry Typography - Completely Transparent Background & Black Typography */}
      <Html position={[0, chassisHeight + 0.35, amrWidth * 0.5 + 0.45]} center distanceFactor={35}>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: "1px",
            fontSize: "7.5px",
            fontFamily: "Inter, system-ui, sans-serif",
            whiteSpace: "nowrap",
            pointerEvents: "none",
            userSelect: "none",
            background: "none",
          }}
        >
          {/* Main Name with Status Glow Dot */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "3.5px",
              background: "none",
            }}
          >
            <span
              style={{
                width: "4.5px",
                height: "4.5px",
                borderRadius: "50%",
                background: statusColor,
                boxShadow: `0 0 4px ${statusColor}`,
              }}
            />
            <span
              style={{
                fontWeight: 800,
                letterSpacing: "0.02em",
                color: "#000000",
                textShadow: "0 0 2px rgba(255,255,255,0.8)",
              }}
            >
              {amr.name || (amr.is_remote ? `📡 ${amr.id}` : amr.id)}
            </span>
          </div>

          {/* Subtitle Status & Battery in Black */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "3px",
              fontSize: "6.5px",
              fontWeight: 700,
              color: "#000000",
              textShadow: "0 0 2px rgba(255,255,255,0.8)",
            }}
          >
            <span>{amr.state_label || "IDLE"}</span>
            <span>• {amr.battery_soc ?? 100}%</span>
            {hasPayload && <span>📦</span>}
          </div>
        </div>
      </Html>





      {/* 9. Heavy Industrial 4-Wheel Assembly */}
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
              <cylinderGeometry args={[wheelRadius, wheelRadius, wheelThickness, 24]} />
              <meshStandardMaterial color="#0f172a" roughness={0.8} />
            </mesh>
            <mesh>
              <cylinderGeometry args={[wheelRadius * 0.65, wheelRadius * 0.65, wheelThickness * 1.05, 16]} />
              <meshStandardMaterial color="#cbd5e1" metalness={0.9} roughness={0.2} />
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

export function Scene({ amrs, selectedNode, onSelectNode }) {
  const [map, setMap] = useState(null);
  const simAmrs = amrs || useSimulationState();

  useEffect(() => {
    fetch(apiUrl("/api/map"))
      .then((res) => res.json())
      .then(setMap);
  }, []);

  if (!map) return null;

  const center = map.nodes.reduce(
    (acc, n) => ({ x: acc.x + n.x / map.nodes.length, y: acc.y + n.y / map.nodes.length }),
    { x: 0, y: 0 }
  );

  return (
    <Canvas
      camera={{ position: [center.x, 14, center.y + 10], fov: 48 }}
      style={{ background: "#f8fafc" }}
    >
      <ambientLight intensity={1.2} />
      <directionalLight position={[8, 18, 8]} intensity={1.0} />

      {/* Clean Seamless Warehouse Floor (Zero Grid Lines) */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[center.x, 0, center.y]}>
        <planeGeometry args={[60, 60]} />
        <meshStandardMaterial
          color="#f1f5f9"
          roughness={0.5}
          metalness={0.05}
        />
      </mesh>

      <MapGeometry
        map={map}
        amrs={simAmrs}
        selectedNode={selectedNode}
        onSelectNode={onSelectNode}
      />
      <AmrModels map={map} amrs={simAmrs} />
      <OrbitControls target={[center.x, 0, center.y]} />
    </Canvas>
  );
}
