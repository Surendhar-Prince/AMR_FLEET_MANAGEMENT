import { Html, OrbitControls } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { DoubleSide, Shape } from "three";
import type { Group } from "three";
import type { AmrState, MapData } from "./types";
import { useSimulationState } from "./useSimulationState";

// A flat 2D arrow (shaft + triangular head) centered at the origin,
// pointing along +X, lying in the shape's local XY plane.
function createArrowShape(length: number, width: number): Shape {
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

// Lays a flat shape (drawn in its local XY plane) down onto the ground
// plane (world XZ), facing up, so it reads as a 2D mark from above.
function FlatArrow({ shape, color }: { shape: Shape; color: string }) {
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]}>
      <shapeGeometry args={[shape]} />
      <meshBasicMaterial color={color} side={DoubleSide} />
    </mesh>
  );
}

const EDGE_ARROW_SHAPE = createArrowShape(0.4, 0.22);

// Deterministic per-id color so each AMR is visually distinct and stable
// across reconnects/reorders, without needing a configured palette.
function colorForId(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  }
  return `hsl(${hash % 360}, 65%, 55%)`;
}

const labelStyle: CSSProperties = {
  padding: "1px 5px",
  borderRadius: 3,
  background: "rgba(20, 24, 30, 0.75)",
  color: "#fff",
  fontSize: 12,
  fontFamily: "monospace",
  whiteSpace: "nowrap",
  pointerEvents: "none",
};

function MapGeometry({ map }: { map: MapData }) {
  const nodeById = useMemo(
    () => new Map(map.nodes.map((n) => [n.id, n])),
    [map.nodes]
  );

  return (
    <group>
      {map.nodes.map((node) => (
        <group key={node.id} position={[node.x, 0.03, node.y]}>
          <mesh rotation={[-Math.PI / 2, 0, 0]}>
            <circleGeometry args={[0.15, 24]} />
            <meshBasicMaterial color="#8899aa" side={DoubleSide} />
          </mesh>
          <Html position={[0, 0.2, 0]} center>
            <div style={labelStyle}>{node.id}</div>
          </Html>
        </group>
      ))}
      {map.edges.map((edge, i) => {
        const from = nodeById.get(edge.from);
        const to = nodeById.get(edge.to);
        if (!from || !to) return null;
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
              <lineBasicMaterial color="#556677" />
            </line>
            <group position={[midX, 0.03, midY]} rotation={[0, -angle, 0]}>
              <FlatArrow shape={EDGE_ARROW_SHAPE} color="#556677" />
            </group>
          </group>
        );
      })}
    </group>
  );
}

// Wheel spin follows the original project's technique: angular velocity =
// linear velocity / wheel radius, integrated over time and applied as
// rotation. Our AMR only ever moves at a constant speed or is stopped (no
// accel/decel), so "linear velocity" is just amr_speed while a path is
// active, 0 otherwise.
function AmrModel({ amr, map }: { amr: AmrState; map: MapData }) {
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
  const chassisColor = amr.colliding ? "#e05252" : identityColor;

  const wheelRefs = useRef<(Group | null)[]>([]);
  const spinRef = useRef(0);
  const isMoving = amr.path.length > 0;

  useFrame((_, delta) => {
    const speed = isMoving ? map.amr_speed : 0;
    spinRef.current += (speed / wheelRadius) * delta;
    for (const wheel of wheelRefs.current) {
      if (wheel) wheel.rotation.z = spinRef.current;
    }
  });

  const wheelPositions: [number, number, number][] = [
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
        <FlatArrow shape={noseArrowShape} color="#f2c744" />
      </group>
      <Html position={[0, chassisHeight + 0.4, 0]} center>
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
              <meshStandardMaterial color="#2b2b2b" />
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

function AmrModels({ map }: { map: MapData }) {
  const amrs = useSimulationState();

  return (
    <group>
      {amrs.map((amr) => (
        <AmrModel key={amr.id} amr={amr} map={map} />
      ))}
    </group>
  );
}

export function Scene() {
  const [map, setMap] = useState<MapData | null>(null);

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
    <Canvas camera={{ position: [center.x, 12, center.y + 8], fov: 50 }}>
      <ambientLight intensity={0.7} />
      <directionalLight position={[5, 10, 5]} intensity={0.6} />
      <MapGeometry map={map} />
      <AmrModels map={map} />
      <OrbitControls target={[center.x, 0, center.y]} />
    </Canvas>
  );
}
