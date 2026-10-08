"use client";

import { Bounds, Center, Grid, OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { useEffect, useState } from "react";
import type { BufferGeometry } from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

/** Interactive viewer for a real STL file fetched from the API (rotate, pan, zoom). */
export function StlViewer({ url, color = "#7c6cff", height = 420 }: { url: string; color?: string; height?: number }) {
  const [geometry, setGeometry] = useState<BufferGeometry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wire, setWire] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setGeometry(null);
    setError(null);
    fetch(url, { credentials: "include" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.arrayBuffer();
      })
      .then((buf) => {
        if (cancelled) return;
        const g = new STLLoader().parse(buf);
        g.computeVertexNormals();
        // CAD is Z-up; three.js is Y-up.
        g.rotateX(-Math.PI / 2);
        setGeometry(g);
      })
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [url]);

  if (error) return <div className="grid place-items-center rounded-[14px] border border-danger/40 text-sm text-danger" style={{ height }}>Could not load model: {error}</div>;
  return (
    <div className="relative overflow-hidden rounded-[14px] border border-border bg-[#0b0e1d]" style={{ height }}>
      {!geometry && <div className="absolute inset-0 grid place-items-center text-sm text-muted">Loading mesh…</div>}
      <Canvas camera={{ position: [120, 100, 140], fov: 40, near: 0.1, far: 5000 }} dpr={[1, 2]}>
        <ambientLight intensity={0.55} />
        <directionalLight position={[200, 300, 150]} intensity={1.3} />
        <directionalLight position={[-200, 100, -150]} intensity={0.4} />
        {geometry && (
          <Bounds fit clip observe margin={1.4}>
            <Center>
              <mesh geometry={geometry} castShadow>
                <meshStandardMaterial color={color} metalness={0.1} roughness={0.55} wireframe={wire} />
              </mesh>
            </Center>
          </Bounds>
        )}
        <Grid args={[400, 400]} cellSize={5} sectionSize={50} cellColor="#262b4d" sectionColor="#3d4380" fadeDistance={600} infiniteGrid position={[0, -0.01, 0]} />
        <OrbitControls makeDefault enableDamping />
      </Canvas>
      <div className="absolute bottom-2 end-2 flex gap-2 text-xs">
        <button className="rounded-md bg-black/50 px-2 py-1 text-white/80 hover:text-white" onClick={() => setWire((w) => !w)}>
          {wire ? "Solid" : "Wireframe"}
        </button>
      </div>
      <div className="pointer-events-none absolute start-3 top-2 text-[11px] text-white/50">Drag to rotate · scroll to zoom · right-drag to pan</div>
    </div>
  );
}
