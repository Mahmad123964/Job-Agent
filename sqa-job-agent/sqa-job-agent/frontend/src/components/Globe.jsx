// Hero accent: low-poly wireframe globe with a few glowing nodes and pulses
// travelling along arcs between them. Ported from the reference's raw
// Three.js <script> to React Three Fiber so it lives in the Vite build.
//
// Loaded via React.lazy from App.jsx (three is ~600 KB) so it never blocks
// the dashboard. Pixel ratio is capped at 2, the render loop stops while the
// tab is hidden, and prefers-reduced-motion renders a single static frame.
import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Line } from "@react-three/drei";
import * as THREE from "three";

const R = 2.1;
const ACCENT = "#22d3ee";
// lat/lon of the glowing nodes: NYC, London, Tokyo, São Paulo, Singapore, Berlin
const NODES = [[40.7, -74], [51.5, -0.1], [35.7, 139.7], [-23.5, -46.6], [1.35, 103.8], [52.5, 13.4]];
const PAIRS = [[0, 1], [1, 2], [2, 4], [3, 0], [5, 4]];

function latLon(lat, lon, r) {
  const p = ((90 - lat) * Math.PI) / 180;
  const t = ((lon + 180) * Math.PI) / 180;
  return new THREE.Vector3(-r * Math.sin(p) * Math.cos(t), r * Math.cos(p), r * Math.sin(p) * Math.sin(t));
}

function makeGlowTexture(color) {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const ctx = c.getContext("2d");
  const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, color);
  g.addColorStop(0.25, color);
  g.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 64, 64);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function useMediaQuery(query) {
  const [matches, setMatches] = useState(() => (typeof window !== "undefined" ? window.matchMedia(query).matches : false));
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = (e) => setMatches(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

function usePageVisible() {
  const [visible, setVisible] = useState(() => document.visibilityState !== "hidden");
  useEffect(() => {
    const onChange = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  return visible;
}

function Scene({ animate }) {
  const group = useRef();
  const pulses = useRef([]);
  const lines = useRef([]);
  const glow = useMemo(() => makeGlowTexture(ACCENT), []);
  const nodes = useMemo(() => NODES.map(([la, lo]) => latLon(la, lo, R)), []);
  const arcs = useMemo(
    () =>
      PAIRS.map(([a, b], i) => {
        const p1 = nodes[a];
        const p2 = nodes[b];
        const mid = p1.clone().add(p2).multiplyScalar(0.5).normalize().multiplyScalar(R * (1.16 + p1.distanceTo(p2) * 0.07));
        const curve = new THREE.QuadraticBezierCurve3(p1, mid, p2);
        const offset = i / PAIRS.length;
        return { curve, points: curve.getPoints(60), offset, start: curve.getPoint(offset) };
      }),
    [nodes]
  );

  useFrame((state) => {
    if (!animate || !group.current) return;
    const t = state.clock.elapsedTime;
    group.current.rotation.y = t * 0.14;
    arcs.forEach((arc, i) => {
      const u = (t * 0.22 + arc.offset) % 1;
      const fade = Math.sin(u * Math.PI);
      const pulse = pulses.current[i];
      if (pulse) {
        pulse.position.copy(arc.curve.getPoint(u));
        pulse.material.opacity = 0.9 * fade;
      }
      const line = lines.current[i];
      if (line) line.material.opacity = 0.14 + 0.22 * fade;
    });
  });

  return (
    <group ref={group} rotation={[0, 0, 0.22]}>
      <mesh>
        <icosahedronGeometry args={[R, 2]} />
        <meshBasicMaterial wireframe color="#8fb6c4" transparent opacity={0.17} />
      </mesh>
      <mesh>
        <icosahedronGeometry args={[R * 1.002, 1]} />
        <meshBasicMaterial wireframe color="#bfe3ec" transparent opacity={0.3} />
      </mesh>

      {nodes.map((p, i) => (
        <group key={i} position={p}>
          <mesh>
            <sphereGeometry args={[0.045, 10, 10]} />
            <meshBasicMaterial color={ACCENT} />
          </mesh>
          <sprite scale={[0.62, 0.62, 0.62]}>
            <spriteMaterial map={glow} transparent blending={THREE.AdditiveBlending} depthWrite={false} opacity={0.75} />
          </sprite>
        </group>
      ))}

      {arcs.map((arc, i) => (
        <group key={i}>
          <Line ref={(el) => (lines.current[i] = el)} points={arc.points} color={ACCENT} transparent opacity={0.28} lineWidth={1} />
          <sprite ref={(el) => (pulses.current[i] = el)} position={arc.start} scale={[0.34, 0.34, 0.34]}>
            <spriteMaterial map={glow} transparent blending={THREE.AdditiveBlending} depthWrite={false} opacity={0.6} />
          </sprite>
        </group>
      ))}
    </group>
  );
}

export default function Globe({ size = 280 }) {
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const visible = usePageVisible();
  const animate = visible && !reducedMotion;

  return (
    <div style={{ width: size, height: size }} aria-hidden="true">
      <Canvas
        dpr={[1, 2]}
        // "demand" renders one frame on mount and then idles — used for the
        // hidden-tab pause and as the reduced-motion static fallback.
        frameloop={animate ? "always" : "demand"}
        camera={{ fov: 38, position: [0, 0.6, 7.2], near: 0.1, far: 100 }}
        gl={{ alpha: true, antialias: true, powerPreference: "low-power" }}
      >
        <Scene animate={animate} />
      </Canvas>
    </div>
  );
}
