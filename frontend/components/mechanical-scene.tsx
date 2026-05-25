"use client";

import { Html, OrbitControls } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { useMemo, useRef, useState } from "react";

type Dimensions = { x_mm: number; y_mm: number; z_mm: number };

type ComponentInstance = {
  ref_des?: string;
  name?: string;
  category?: string;
  part_number?: string;
  rationale?: string;
};

type VectorLike =
  | Partial<Dimensions>
  | {
      x?: number;
      y?: number;
      z?: number;
      x_deg?: number;
      y_deg?: number;
      z_deg?: number;
    };

type PlacementInput = {
  ref_des?: string;
  ref?: string;
  label?: string;
  category?: string;
  layer?: string;
  position?: VectorLike;
  position_mm?: VectorLike;
  size?: VectorLike;
  size_mm?: VectorLike;
  orientation_deg?: VectorLike;
  rotation_deg?: VectorLike;
  mounting_face?: string;
  notes?: string;
};

type SpatialRelationshipInput = {
  source_ref_des?: string;
  source?: string;
  target_ref_des?: string;
  target?: string;
  relation?: string;
  axis?: string;
  offset_mm?: number;
  notes?: string;
};

type MechanicalSceneProps = {
  dimensions: Dimensions;
  components: ComponentInstance[];
  placements?: PlacementInput[];
  relationships?: SpatialRelationshipInput[];
  features: string[];
  toggles: Record<string, boolean>;
  electricalActive: boolean;
};

type ScenePlacement = {
  refDes: string;
  label: string;
  category: string;
  layer: string;
  positionMm: [number, number, number];
  sizeMm: [number, number, number];
  rotationRad: [number, number, number];
  color: string;
  accent: string;
  component?: ComponentInstance;
  notes?: string;
};

type SceneRelationship = {
  id: string;
  sourceRef: string;
  targetRef: string;
  relation: string;
  axis: "X" | "Y" | "Z";
  offsetMm?: number;
  notes?: string;
};

const categoryPalette: Record<string, { color: string; accent: string; layer: string }> = {
  microcontroller: { color: "#22d3ee", accent: "#cffafe", layer: "electrical" },
  sensor: { color: "#34d399", accent: "#d1fae5", layer: "electrical" },
  actuator: { color: "#fb923c", accent: "#ffedd5", layer: "electrical" },
  display: { color: "#ec4899", accent: "#fce7f3", layer: "electrical" },
  power: { color: "#facc15", accent: "#fef9c3", layer: "electrical" },
  passives: { color: "#a78bfa", accent: "#ede9fe", layer: "electrical" },
  communication: { color: "#60a5fa", accent: "#dbeafe", layer: "electrical" },
  mechanical: { color: "#fb7185", accent: "#ffe4e6", layer: "mechanism" },
  "3d print": { color: "#818cf8", accent: "#e0e7ff", layer: "print" },
  default: { color: "#94a3b8", accent: "#e2e8f0", layer: "electrical" },
};

const categorySizes: Record<string, [number, number, number]> = {
  microcontroller: [38, 28, 5],
  sensor: [20, 18, 8],
  actuator: [30, 24, 14],
  display: [32, 18, 4],
  power: [42, 24, 8],
  passives: [14, 14, 7],
  communication: [28, 20, 5],
  mechanical: [14, 14, 8],
  "3d print": [26, 20, 8],
  default: [22, 18, 6],
};

function categoryKey(category?: string) {
  return String(category || "default").trim().toLowerCase();
}

function isMechanicalCategory(category?: string) {
  const key = categoryKey(category);
  return key === "mechanical" || key === "3d print";
}

function isEnclosureLabel(label: string) {
  const normalized = label.toLowerCase();
  if (/screw|insert|standoff|button cap|fastener/.test(normalized)) return false;
  return /main enclosure|enclosure shell|project box|\bshell\b|\bhousing\b|\bcase\b|\bcover\b|\bbody\b/.test(normalized);
}

function getVectorValue(vector: VectorLike | undefined, axis: "x" | "y" | "z", fallback: number) {
  if (!vector) return fallback;
  const mmKey = `${axis}_mm` as keyof Dimensions;
  const degKey = `${axis}_deg` as "x_deg" | "y_deg" | "z_deg";
  const raw = (vector as Partial<Dimensions>)[mmKey] ?? (vector as { x?: number; y?: number; z?: number })[axis] ?? (vector as { x_deg?: number; y_deg?: number; z_deg?: number })[degKey];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : fallback;
}

function parseVector(vector: VectorLike | undefined, fallback: [number, number, number]): [number, number, number] {
  return [
    getVectorValue(vector, "x", fallback[0]),
    getVectorValue(vector, "y", fallback[1]),
    getVectorValue(vector, "z", fallback[2]),
  ];
}

function degreesToRadians(rotation: [number, number, number]): [number, number, number] {
  return rotation.map((value) => THREE.MathUtils.degToRad(value)) as [number, number, number];
}

function placementPalette(category: string) {
  return categoryPalette[categoryKey(category)] || categoryPalette.default;
}

function placementSize(component: ComponentInstance): [number, number, number] {
  const key = categoryKey(component.category);
  const name = `${component.name || ""} ${component.part_number || ""}`.toLowerCase();

  if (name.includes("battery")) return [48, 26, 8];
  if (name.includes("speaker")) return [24, 24, 10];
  if (name.includes("relay")) return [38, 26, 16];
  if (name.includes("oled") || name.includes("display")) return [34, 18, 4];
  if (name.includes("button") || name.includes("switch")) return [10, 10, 7];
  if (name.includes("screw") || name.includes("insert")) return [5, 5, 8];

  return categorySizes[key] || categorySizes.default;
}

function generatedPosition(component: ComponentInstance, index: number, components: ComponentInstance[], dimensions: Dimensions): [number, number, number] {
  const key = categoryKey(component.category);
  const name = `${component.name || ""} ${component.part_number || ""}`.toLowerCase();
  const electrical = components.filter((item) => !isMechanicalCategory(item.category));
  const electricalIndex = Math.max(0, electrical.findIndex((item) => item.ref_des === component.ref_des));
  const printParts = components.filter((item) => categoryKey(item.category) === "3d print");
  const mechParts = components.filter((item) => categoryKey(item.category) === "mechanical");

  const floorZ = -dimensions.z_mm * 0.28;
  const midZ = -dimensions.z_mm * 0.05;
  const topZ = dimensions.z_mm * 0.3;

  if (key === "microcontroller") return [0, 0, midZ];
  if (key === "display") return [0, -dimensions.y_mm * 0.4, topZ];
  if (key === "power") {
    const powerOffset = electrical.filter((item) => categoryKey(item.category) === "power").findIndex((item) => item.ref_des === component.ref_des);
    return [-dimensions.x_mm * 0.28 + powerOffset * Math.min(28, dimensions.x_mm * 0.18), dimensions.y_mm * 0.22, floorZ];
  }
  if (key === "sensor") {
    return [-dimensions.x_mm * 0.28 + electricalIndex * Math.min(18, dimensions.x_mm * 0.12), -dimensions.y_mm * 0.18, midZ];
  }
  if (key === "actuator") {
    return [dimensions.x_mm * 0.28, dimensions.y_mm * 0.15 - electricalIndex * Math.min(8, dimensions.y_mm * 0.12), midZ];
  }
  if (key === "passives" || key === "communication") {
    const count = Math.max(1, electrical.length - 1);
    const t = electricalIndex / count;
    return [
      -dimensions.x_mm * 0.34 + t * dimensions.x_mm * 0.68,
      -dimensions.y_mm * 0.34,
      floorZ + (electricalIndex % 2) * Math.min(10, dimensions.z_mm * 0.16),
    ];
  }
  if (key === "3d print") {
    const printIndex = Math.max(0, printParts.findIndex((item) => item.ref_des === component.ref_des));
    const isShell = isEnclosureLabel(`${component.name || ""} ${component.part_number || ""}`);
    if (isShell) return [0, 0, 0];
    return [
      -dimensions.x_mm * 0.36 + printIndex * Math.min(20, dimensions.x_mm * 0.16),
      dimensions.y_mm * 0.38,
      dimensions.z_mm * 0.22 - (printIndex % 2) * Math.min(18, dimensions.z_mm * 0.3),
    ];
  }
  if (key === "mechanical") {
    const mechIndex = Math.max(0, mechParts.findIndex((item) => item.ref_des === component.ref_des));
    const cornerX = mechIndex % 2 === 0 ? -dimensions.x_mm * 0.42 : dimensions.x_mm * 0.42;
    const cornerY = mechIndex < 2 ? -dimensions.y_mm * 0.36 : dimensions.y_mm * 0.36;
    return [cornerX, cornerY, floorZ + (mechIndex % 3) * 5];
  }

  const columns = Math.ceil(Math.sqrt(Math.max(components.length, 1)));
  const col = index % columns;
  const row = Math.floor(index / columns);
  return [
    (col / Math.max(columns - 1, 1) - 0.5) * dimensions.x_mm * 0.68,
    (row / Math.max(columns - 1, 1) - 0.5) * dimensions.y_mm * 0.6,
    midZ,
  ];
}

function normalizeProvidedPlacements(placements: PlacementInput[], components: ComponentInstance[], dimensions: Dimensions) {
  const componentByRef = new Map(components.map((component) => [component.ref_des, component]));
  const parsed = placements
    .map((placement) => {
      const refDes = placement.ref_des || placement.ref;
      if (!refDes) return null;

      const component = componentByRef.get(refDes);
      const label = placement.label || component?.name || refDes;
      const category = placement.category || component?.category || "default";
      const palette = placementPalette(category);
      const sizeMm = parseVector(placement.size_mm || placement.size, component ? placementSize(component) : categorySizes.default);
      const rotationRad = degreesToRadians(parseVector(placement.orientation_deg || placement.rotation_deg, [0, 0, 0]));
      const positionMm = parseVector(placement.position_mm || placement.position, [0, 0, 0]);

      return {
        refDes,
        label,
        category,
        layer: placement.layer || palette.layer,
        positionMm,
        sizeMm,
        rotationRad,
        color: palette.color,
        accent: palette.accent,
        component,
        notes: placement.notes || placement.mounting_face,
      } satisfies ScenePlacement;
    })
    .filter(Boolean) as ScenePlacement[];

  const usesPositiveOrigin =
    parsed.length > 0 &&
    parsed.every((placement) => {
      const [x, y, z] = placement.positionMm;
      return x >= 0 && y >= 0 && z >= 0 && x <= dimensions.x_mm && y <= dimensions.y_mm && z <= dimensions.z_mm;
    });

  if (!usesPositiveOrigin) return parsed;

  return parsed.map((placement) => {
    const positionMm: [number, number, number] = [
      placement.positionMm[0] - dimensions.x_mm / 2,
      placement.positionMm[1] - dimensions.y_mm / 2,
      placement.positionMm[2] - dimensions.z_mm / 2,
    ];

    return {
      ...placement,
      positionMm,
    };
  });
}

function buildScenePlacements(dimensions: Dimensions, components: ComponentInstance[], providedPlacements: PlacementInput[]): ScenePlacement[] {
  const normalized = normalizeProvidedPlacements(providedPlacements, components, dimensions);
  const placementByRef = new Map(normalized.map((placement) => [placement.refDes, placement]));

  components.forEach((component, index) => {
    const refDes = component.ref_des || `C${index + 1}`;
    if (placementByRef.has(refDes)) return;

    const palette = placementPalette(component.category || "default");
    const label = component.name || component.part_number || refDes;
    const category = component.category || "default";
    placementByRef.set(refDes, {
      refDes,
      label,
      category,
      layer: isEnclosureLabel(label) ? "enclosure" : palette.layer,
      positionMm: generatedPosition(component, index, components, dimensions),
      sizeMm: placementSize(component),
      rotationRad: [0, 0, 0],
      color: palette.color,
      accent: palette.accent,
      component,
      notes: component.rationale,
    });
  });

  return Array.from(placementByRef.values());
}

function dominantAxis(source: ScenePlacement, target: ScenePlacement): "X" | "Y" | "Z" {
  const deltas = [
    { axis: "X" as const, value: Math.abs(target.positionMm[0] - source.positionMm[0]) },
    { axis: "Y" as const, value: Math.abs(target.positionMm[1] - source.positionMm[1]) },
    { axis: "Z" as const, value: Math.abs(target.positionMm[2] - source.positionMm[2]) },
  ];
  return deltas.sort((a, b) => b.value - a.value)[0]?.axis || "X";
}

function normalizeRelationships(inputs: SpatialRelationshipInput[], placements: ScenePlacement[]) {
  const placementByRef = new Map(placements.map((placement) => [placement.refDes, placement]));
  const explicit = inputs
    .map((relationship, index) => {
      const sourceRef = relationship.source_ref_des || relationship.source;
      const targetRef = relationship.target_ref_des || relationship.target;
      if (!sourceRef || !targetRef || !placementByRef.has(sourceRef) || !placementByRef.has(targetRef)) return null;

      const axis = String(relationship.axis || dominantAxis(placementByRef.get(sourceRef)!, placementByRef.get(targetRef)!)).toUpperCase();
      return {
        id: `${sourceRef}-${targetRef}-${index}`,
        sourceRef,
        targetRef,
        relation: relationship.relation || "relative placement",
        axis: axis === "Y" || axis === "Z" ? axis : "X",
        offsetMm: relationship.offset_mm,
        notes: relationship.notes,
      } satisfies SceneRelationship;
    })
    .filter(Boolean) as SceneRelationship[];

  if (explicit.length > 0) return explicit;

  const controller = placements.find((placement) => categoryKey(placement.category) === "microcontroller") || placements.find((placement) => !isMechanicalCategory(placement.category));
  if (!controller) return [];

  return placements
    .filter((placement) => placement.refDes !== controller.refDes && !isEnclosureLabel(placement.label) && !isMechanicalCategory(placement.category))
    .slice(0, 6)
    .map((placement, index) => {
      const axis = dominantAxis(controller, placement);
      const sourcePosition = controller.positionMm[axis === "X" ? 0 : axis === "Y" ? 1 : 2];
      const targetPosition = placement.positionMm[axis === "X" ? 0 : axis === "Y" ? 1 : 2];
      return {
        id: `${controller.refDes}-${placement.refDes}-${index}`,
        sourceRef: controller.refDes,
        targetRef: placement.refDes,
        relation: "spatial offset",
        axis,
        offsetMm: Math.round(targetPosition - sourcePosition),
      } satisfies SceneRelationship;
    });
}

function worldPosition(positionMm: [number, number, number], scale: number): [number, number, number] {
  const [xMm, yMm, zMm] = positionMm;
  return [xMm / scale, zMm / scale, yMm / scale];
}

function worldSize(sizeMm: [number, number, number], scale: number): [number, number, number] {
  const [xMm, yMm, zMm] = sizeMm;
  return [
    Math.max(xMm / scale, 0.16),
    Math.max(zMm / scale, 0.08),
    Math.max(yMm / scale, 0.16),
  ];
}

function axisColor(axis: "X" | "Y" | "Z") {
  if (axis === "X") return "#f87171";
  if (axis === "Y") return "#22d3ee";
  return "#facc15";
}

function SceneShell({ dimensions, scale }: { dimensions: Dimensions; scale: number }) {
  const shellRef = useRef<THREE.Group>(null);

  useFrame(({ clock }) => {
    if (!shellRef.current) return;
    shellRef.current.rotation.y = Math.sin(clock.elapsedTime * 0.24) * 0.018;
  });

  const width = dimensions.x_mm / scale;
  const height = dimensions.z_mm / scale;
  const depth = dimensions.y_mm / scale;

  return (
    <group ref={shellRef}>
      <mesh receiveShadow>
        <boxGeometry args={[width, height, depth]} />
        <meshStandardMaterial color="#8b5cf6" metalness={0.12} roughness={0.55} transparent opacity={0.09} />
      </mesh>
      <mesh>
        <boxGeometry args={[width, height, depth]} />
        <meshBasicMaterial color="#c4b5fd" wireframe transparent opacity={0.32} />
      </mesh>
      <mesh position={[0, -height / 2 - 0.04, 0]} receiveShadow>
        <boxGeometry args={[width * 0.98, 0.08, depth * 0.98]} />
        <meshStandardMaterial color="#0e1015" metalness={0.08} roughness={0.85} transparent opacity={0.72} />
      </mesh>
    </group>
  );
}

function AxisMeasure({
  axis,
  value,
  dimensions,
  scale,
}: {
  axis: "X" | "Y" | "Z";
  value: number;
  dimensions: Dimensions;
  scale: number;
}) {
  const width = dimensions.x_mm / scale;
  const height = dimensions.z_mm / scale;
  const depth = dimensions.y_mm / scale;
  const color = axisColor(axis);

  const lineArgs: [number, number, number] =
    axis === "X" ? [width, 0.035, 0.035] : axis === "Y" ? [0.035, 0.035, depth] : [0.035, height, 0.035];
  const position: [number, number, number] =
    axis === "X"
      ? [0, -height / 2 - 0.55, -depth / 2 - 0.42]
      : axis === "Y"
        ? [-width / 2 - 0.5, -height / 2 - 0.55, 0]
        : [width / 2 + 0.5, 0, -depth / 2 - 0.42];
  const labelPosition: [number, number, number] =
    axis === "X"
      ? [0, -height / 2 - 0.95, -depth / 2 - 0.42]
      : axis === "Y"
        ? [-width / 2 - 1.0, -height / 2 - 0.95, 0]
        : [width / 2 + 1.0, height / 2 + 0.35, -depth / 2 - 0.42];

  return (
    <group>
      <mesh position={position}>
        <boxGeometry args={lineArgs} />
        <meshBasicMaterial color={color} />
      </mesh>
      <Html transform center position={labelPosition} distanceFactor={8}>
        <div className="pointer-events-none border border-white/10 bg-black/80 px-2.5 py-1 text-center font-black uppercase tracking-[0.16em] shadow-xl">
          <div style={{ color }} className="text-[12px] leading-none">
            {axis} axis
          </div>
          <div className="mt-1 text-[10px] leading-none text-white/80">{value}mm</div>
        </div>
      </Html>
    </group>
  );
}

function ModuleBlock({
  spec,
  scale,
  selected,
  onSelect,
}: {
  spec: ScenePlacement;
  scale: number;
  selected: boolean;
  onSelect: (placement: ScenePlacement) => void;
}) {
  const blockRef = useRef<THREE.Group>(null);
  const position = worldPosition(spec.positionMm, scale);
  const size = worldSize(spec.sizeMm, scale);

  useFrame(({ clock }) => {
    if (!blockRef.current) return;
    blockRef.current.position.y = position[1] + Math.sin(clock.elapsedTime * 1.4 + spec.positionMm[0] * 0.03) * 0.018;
  });

  return (
    <group ref={blockRef} position={position} rotation={spec.rotationRad}>
      <mesh
        castShadow
        receiveShadow
        onClick={(event) => {
          event.stopPropagation();
          onSelect(spec);
        }}
        onPointerOver={(event) => {
          event.stopPropagation();
          document.body.style.cursor = "pointer";
        }}
        onPointerOut={() => {
          document.body.style.cursor = "";
        }}
      >
        <boxGeometry args={size} />
        <meshStandardMaterial
          color={spec.color}
          metalness={0.28}
          roughness={0.34}
          emissive={selected ? spec.accent : spec.color}
          emissiveIntensity={selected ? 0.36 : 0.08}
        />
      </mesh>
      <mesh>
        <boxGeometry args={[size[0] * 1.04, size[1] * 1.04, size[2] * 1.04]} />
        <meshBasicMaterial color={selected ? "#ffffff" : spec.accent} wireframe transparent opacity={selected ? 0.72 : 0.34} />
      </mesh>
      <Html transform center position={[0, size[1] * 0.95 + 0.1, 0]} distanceFactor={9}>
        <button
          type="button"
          onClick={() => onSelect(spec)}
          className={`pointer-events-auto max-w-[160px] border px-2 py-1 text-[9px] font-black uppercase tracking-[0.14em] shadow-lg ${
            selected ? "border-white bg-white text-black" : "border-white/15 bg-black/85 text-slate-100"
          }`}
          title={`${spec.refDes}: ${spec.label}`}
        >
          <span className="block truncate">{spec.refDes}</span>
          {selected && <span className="block truncate text-[8px] opacity-70">{spec.label}</span>}
        </button>
      </Html>
    </group>
  );
}

function RelationshipLink({
  relationship,
  placements,
  scale,
}: {
  relationship: SceneRelationship;
  placements: Map<string, ScenePlacement>;
  scale: number;
}) {
  const source = placements.get(relationship.sourceRef);
  const target = placements.get(relationship.targetRef);
  const geometry = useMemo(() => {
    if (!source || !target) return null;

    const start = new THREE.Vector3(...worldPosition(source.positionMm, scale));
    const end = new THREE.Vector3(...worldPosition(target.positionMm, scale));
    const midpoint = start.clone().add(end).multiplyScalar(0.5);
    const direction = end.clone().sub(start);
    const length = direction.length();
    const quaternion = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.clone().normalize());
    return { midpoint, quaternion, length };
  }, [scale, source, target]);

  if (!source || !target || !geometry || geometry.length < 0.1) return null;

  const color = axisColor(relationship.axis);

  return (
    <group>
      <group position={geometry.midpoint} quaternion={geometry.quaternion}>
        <mesh>
          <cylinderGeometry args={[0.012, 0.012, geometry.length, 8]} />
          <meshBasicMaterial color={color} transparent opacity={0.7} />
        </mesh>
      </group>
      <Html transform center position={geometry.midpoint.toArray() as [number, number, number]} distanceFactor={10}>
        <div className="pointer-events-none whitespace-nowrap border border-white/10 bg-black/75 px-2 py-1 text-[9px] font-black uppercase tracking-[0.12em] text-white/80 shadow-lg">
          <span style={{ color }}>{relationship.axis}</span>
          <span className="mx-1 text-white/35">/</span>
          {relationship.offsetMm !== undefined ? `${relationship.offsetMm}mm` : relationship.relation}
        </div>
      </Html>
    </group>
  );
}

function visiblePlacement(placement: ScenePlacement, toggles: Record<string, boolean>, electricalActive: boolean) {
  const key = categoryKey(placement.category);
  const layer = placement.layer.toLowerCase();

  if (layer === "enclosure" || isEnclosureLabel(placement.label)) return Boolean(toggles.enclosure);
  if (layer === "structural") return Boolean(toggles.structural);
  if (key === "3d print" || layer === "print") return Boolean(toggles.print);
  if (key === "mechanical" || layer === "mechanism") return Boolean(toggles.mechanism);
  if (layer === "misc") return Boolean(toggles.misc);
  return electricalActive;
}

export default function MechanicalScene({
  dimensions,
  components,
  placements = [],
  relationships = [],
  features,
  toggles,
  electricalActive,
}: MechanicalSceneProps) {
  const [selectedRef, setSelectedRef] = useState<string | null>(null);
  const scale = Math.max(Math.max(dimensions.x_mm, dimensions.y_mm, dimensions.z_mm) / 9.6, 8);
  const scenePlacements = useMemo(() => buildScenePlacements(dimensions, components, placements), [components, dimensions, placements]);
  const visiblePlacements = useMemo(
    () => scenePlacements.filter((placement) => visiblePlacement(placement, toggles, electricalActive)),
    [electricalActive, scenePlacements, toggles]
  );
  const visiblePlacementMap = useMemo(() => new Map(visiblePlacements.map((placement) => [placement.refDes, placement])), [visiblePlacements]);
  const sceneRelationships = useMemo(() => normalizeRelationships(relationships, visiblePlacements), [relationships, visiblePlacements]);
  const selectedPlacement = visiblePlacements.find((placement) => placement.refDes === selectedRef) || visiblePlacements[0] || null;
  const shellLabel = scenePlacements.find((placement) => placement.layer === "enclosure" || isEnclosureLabel(placement.label))?.label || "Mechanical envelope";

  return (
    <div className="relative h-full overflow-hidden bg-[#141519]">
      <Canvas camera={{ position: [9.5, 6.8, 10.5], fov: 40 }} shadows dpr={[1, 2]} onPointerMissed={() => setSelectedRef(null)}>
        <color attach="background" args={["#141519"]} />
        <fog attach="fog" args={["#141519", 18, 42]} />
        <ambientLight intensity={0.72} />
        <directionalLight position={[9, 12, 8]} intensity={1.6} color="#f5edff" castShadow />
        <directionalLight position={[-8, 4, -6]} intensity={0.55} color="#60a5fa" />

        <group position={[0, 0.1, 0]}>
          {toggles.structural && <gridHelper args={[42, 42, "#2b2f39", "#1f232b"]} position={[0, -dimensions.z_mm / scale / 2 - 0.72, 0]} />}
          {toggles.enclosure && <SceneShell dimensions={dimensions} scale={scale} />}

          {visiblePlacements
            .filter((placement) => !(placement.layer === "enclosure" || isEnclosureLabel(placement.label)))
            .map((placement) => (
              <ModuleBlock
                key={placement.refDes}
                spec={placement}
                scale={scale}
                selected={placement.refDes === selectedPlacement?.refDes}
                onSelect={(nextPlacement) => setSelectedRef(nextPlacement.refDes)}
              />
            ))}

          {toggles.structural &&
            sceneRelationships.map((relationship) => (
              <RelationshipLink key={relationship.id} relationship={relationship} placements={visiblePlacementMap} scale={scale} />
            ))}

          <AxisMeasure axis="X" value={dimensions.x_mm} dimensions={dimensions} scale={scale} />
          <AxisMeasure axis="Y" value={dimensions.y_mm} dimensions={dimensions} scale={scale} />
          <AxisMeasure axis="Z" value={dimensions.z_mm} dimensions={dimensions} scale={scale} />

          {toggles.print && (
            <Html transform center position={[0, dimensions.z_mm / scale / 2 + 0.8, 0]} distanceFactor={10}>
              <div className="pointer-events-none text-center font-black uppercase tracking-[0.16em] text-white drop-shadow-[0_0_12px_rgba(0,0,0,0.7)]">
                <div className="text-base">{shellLabel}</div>
                <div className="mt-1 text-[10px] text-violet-200">Component placement mapped to X / Y / Z coordinates</div>
              </div>
            </Html>
          )}
        </group>

        <OrbitControls enableDamping enablePan minPolarAngle={0.45} maxPolarAngle={1.38} minDistance={7} maxDistance={22} autoRotate autoRotateSpeed={0.26} />
      </Canvas>

      <div className="pointer-events-none absolute left-5 top-5 border border-[#30323a] bg-black/70 px-3 py-2 text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">
        <span className="text-white">Live 3D</span>
        <span className="mx-2 text-slate-700">/</span>
        Three.js + R3F
      </div>

      {selectedPlacement && (
        <div className="pointer-events-none absolute bottom-5 left-5 max-w-[340px] border border-[#30323a] bg-black/78 p-3 shadow-2xl">
          <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.18em]" style={{ color: selectedPlacement.color }}>
            <span>{selectedPlacement.refDes}</span>
            <span className="text-slate-700">/</span>
            <span>{selectedPlacement.category}</span>
          </div>
          <div className="mt-2 truncate text-sm font-black uppercase tracking-[0.12em] text-white">{selectedPlacement.label}</div>
          <div className="mt-3 grid grid-cols-3 gap-2 text-[10px] font-black uppercase tracking-[0.12em] text-slate-500">
            <div className="flex justify-between"><span>X</span><span>{Math.round(selectedPlacement.positionMm[0])}mm</span></div>
            <div className="flex justify-between"><span>Y</span><span>{Math.round(selectedPlacement.positionMm[1])}mm</span></div>
            <div className="flex justify-between"><span>Z</span><span>{Math.round(selectedPlacement.positionMm[2])}mm</span></div>
          </div>
        </div>
      )}

      <div className="pointer-events-none absolute bottom-6 right-8 max-w-sm text-right text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">
        {features.slice(0, 4).join(" / ")}
      </div>
    </div>
  );
}
