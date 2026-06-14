"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  Node,
  Edge,
  Handle,
  NodeProps,
  Position,
  useNodesState,
  useEdgesState,
  MarkerType,
} from "reactflow";
import "reactflow/dist/style.css";
import MechanicalScene from "../components/mechanical-scene";
import {
  Sparkles,
  Wrench,
  Cpu,
  ShieldCheck,
  AlertTriangle,
  CheckCircle,
  ShoppingBag,
  History,
  Box,
  RefreshCw,
  Eye,
  Download,
  Database,
  ArrowRight,
  Battery,
  Monitor,
  Printer,
  Sliders,
  Info,
  Layers,
  Volume2,
  Paperclip,
  X,
  ExternalLink,
} from "lucide-react";

const API_URL = "http://localhost:8000";
const JOB_POLL_INTERVAL_MS = 5000;

const samplePrompts = [
  "Compact handheld device with display, controls, USB-C power, and enclosure",
  "Environmental monitor with sensor feedback, display, and battery power",
  "Small controller for a low-voltage actuator or relay",
];

const communityProjects = [
  {
    title: "Portable device",
    description: "Reference design for a compact handheld product with display, controls, and enclosure notes.",
    file: "pocket_mp3_player.json",
  },
  {
    title: "Monitoring kit",
    description: "General-purpose sensing and control example with power, wiring, and enclosure guidance.",
    file: "plant_watering.json",
  },
  {
    title: "Control module",
    description: "Compact controller example with display, sensor, and validated power rails.",
    file: "smart_thermostat.json",
  },
];

const pipelineMermaidCode = `graph LR
  IMAGE["Image Input"] --> FEATURES["Feature Extraction"]
  FEATURES --> IR["Typed Hardware IR (Pydantic JSON)"]
  IR --> BOM["BOM"]
  IR --> CAD["Mechanical CAD"]`;

const workspaceTabs = [
  { id: "overview", label: "IMAGE", icon: Eye },
  { id: "bom", label: "BOM", icon: ShoppingBag },
  { id: "mechanical", label: "MECH", icon: Box },
  { id: "schematic", label: "WIRE", icon: Cpu },
  { id: "assembly", label: "DOCS", icon: Info },
  { id: "svg", label: "SVG", icon: Layers },
  { id: "jobs", label: "JOBS", icon: History },
];

function normalizeTab(tab: string | null) {
  if (!tab) return null;
  const aliases: Record<string, string> = {
    image: "overview",
    mech: "mechanical",
    wire: "schematic",
    docs: "assembly",
  };
  const normalized = aliases[tab] || tab;
  return workspaceTabs.some((item) => item.id === normalized) ? normalized : null;
}

const categoryTone: Record<string, { text: string; bg: string; border: string; label: string }> = {
  microcontroller: { text: "text-cyan-400", bg: "bg-cyan-950/40", border: "border-cyan-500/40", label: "MCU" },
  sensor: { text: "text-emerald-400", bg: "bg-emerald-950/30", border: "border-emerald-500/30", label: "SENSOR" },
  actuator: { text: "text-orange-400", bg: "bg-orange-950/35", border: "border-orange-500/40", label: "ACTUATOR" },
  display: { text: "text-pink-400", bg: "bg-pink-950/35", border: "border-pink-500/40", label: "DISPLAY" },
  power: { text: "text-yellow-400", bg: "bg-yellow-950/35", border: "border-yellow-500/40", label: "POWER" },
  passives: { text: "text-violet-400", bg: "bg-violet-950/35", border: "border-violet-500/40", label: "IO" },
  mechanical: { text: "text-rose-400", bg: "bg-rose-950/30", border: "border-rose-500/35", label: "MECH" },
  "3d print": { text: "text-indigo-300", bg: "bg-indigo-950/35", border: "border-indigo-400/35", label: "3D PRINT" },
  default: { text: "text-slate-300", bg: "bg-slate-900", border: "border-slate-700", label: "PART" },
};

type SchematicPin = {
  pin_id: string;
  name?: string;
  pin_type?: string;
  voltage?: number | null;
};

type A2AJob = {
  job_id: string;
  message_id?: string;
  correlation_id?: string | null;
  action: string;
  sender: string;
  recipient: string;
  status: string;
  server_owned?: boolean;
  created_at?: string;
  updated_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
  payload?: Record<string, any>;
  result_summary?: Record<string, any> | null;
  error?: string | null;
};

type SchematicNodeData = {
  component: any;
  pins: SchematicPin[];
  tone: {
    label: string;
    border: string;
    text: string;
    soft: string;
  };
};

type PlacementPoint = {
  x: number;
  y: number;
};

const schematicTones: Record<string, { label: string; border: string; text: string; soft: string }> = {
  microcontroller: { label: "MCU", border: "#22c7dd", text: "#06b6d4", soft: "#ecfeff" },
  sensor: { label: "SENSOR", border: "#3b82f6", text: "#2563eb", soft: "#eff6ff" },
  actuator: { label: "ACTUATOR", border: "#ff6b21", text: "#ea580c", soft: "#fff7ed" },
  power: { label: "POWER", border: "#f5a400", text: "#d97706", soft: "#fffbeb" },
  passives: { label: "MODULE", border: "#8b5cf6", text: "#7c3aed", soft: "#f5f3ff" },
  communication: { label: "MODULE", border: "#8b5cf6", text: "#7c3aed", soft: "#f5f3ff" },
  display: { label: "DISPLAY", border: "#ec4899", text: "#db2777", soft: "#fdf2f8" },
  default: { label: "PART", border: "#94a3b8", text: "#64748b", soft: "#f8fafc" },
};

const schematicNodeTypes = {
  schematicPart: SchematicPartNode,
};

function SchematicPartNode({ data }: NodeProps<SchematicNodeData>) {
  const { component, pins, tone } = data;
  const Icon = iconForCategory(component.category);
  const visiblePins = pins.length ? pins : [{ pin_id: "NC", name: "No connected pins", pin_type: "Passive" }];

  return (
    <div className="schematic-node w-[190px] bg-white px-3 py-3 text-center shadow-sm" style={{ border: `2px solid ${tone.border}` }}>
      <div className="text-[8px] font-black uppercase leading-none tracking-[0.22em]" style={{ color: tone.text }}>
        {tone.label}
      </div>
      <div className="mt-1 truncate text-[11px] font-black leading-tight text-[#202127]">{component.name || component.ref_des}</div>
      <div className="mt-1 truncate text-[8px] font-bold leading-tight text-[#6f7280]">{component.part_number || component.ref_des}</div>

      <div className="mx-auto mt-2 flex h-[76px] w-[108px] items-center justify-center border border-[#d9dcec] bg-white" style={{ backgroundColor: tone.soft }}>
        <Icon className="h-10 w-10" style={{ color: tone.text }} />
      </div>

      <div className="mt-2 flex flex-wrap justify-center gap-1">
        {visiblePins.map((pin) => {
          const disabled = pin.pin_id === "NC";
          return (
            <div
              key={pin.pin_id}
              className="relative max-w-full rounded-[3px] border bg-white px-1.5 py-0.5 text-[7px] font-black leading-none text-[#6f7280]"
              style={{ borderColor: tone.border, color: disabled ? "#a8adba" : tone.text }}
              title={`${pin.pin_id}${pin.name ? ` - ${pin.name}` : ""}`}
            >
              {!disabled && (
                <>
                  <Handle
                    type="target"
                    id={schematicHandleId(component.ref_des, pin.pin_id)}
                    position={Position.Left}
                    className="schematic-pin-handle"
                    style={{ left: -7, top: "50%", ["--handle-border" as string]: tone.border, ["--handle-color" as string]: "#ffffff" }}
                  />
                  <Handle
                    type="source"
                    id={schematicHandleId(component.ref_des, pin.pin_id)}
                    position={Position.Right}
                    className="schematic-pin-handle"
                    style={{ right: -7, top: "50%", ["--handle-border" as string]: tone.border, ["--handle-color" as string]: "#ffffff" }}
                  />
                </>
              )}
              <span className="block max-w-[72px] truncate">{pin.pin_id}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function schematicToneForCategory(category = "") {
  return schematicTones[category.toLowerCase()] || schematicTones.default;
}

function pinKey(pin: SchematicPin) {
  return pin.pin_id;
}

function schematicHandleId(refDes: string, pinId: string) {
  return `${refDes}.${pinId}`;
}

function withProjectResponseMetadata(ir: any, response: any) {
  if (!ir) return ir;
  return {
    ...ir,
    assembly_metadata: {
      ...(ir.assembly_metadata || {}),
      project_id: ir.assembly_metadata?.project_id || response?.project_id,
      frontend_job_id: ir.assembly_metadata?.frontend_job_id || response?.job_id,
    },
  };
}

function projectIdFromIR(ir: any) {
  return ir?.assembly_metadata?.project_id || null;
}

function projectRoute(projectId: string) {
  return `/project/${encodeURIComponent(projectId)}`;
}

function normalizePlacement(value: any): PlacementPoint | null {
  if (!value || typeof value.x !== "number" || typeof value.y !== "number") return null;
  return { x: value.x, y: value.y };
}

type HomeProps = {
  routeProjectId?: string | null;
};

export function BlueprintWorkspace({ routeProjectId = null }: HomeProps = {}) {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [activeTab, setActiveTab] = useState("overview");
  const [projectIR, setProjectIR] = useState<any>(null);
  const [mermaidCode, setMermaidCode] = useState<string>("");
  const [svgSchematic, setSvgSchematic] = useState<string>("");
  const [projectHistory, setProjectHistory] = useState<any[]>([]);
  const [a2aJobs, setA2aJobs] = useState<A2AJob[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const [jobStatusFilter, setJobStatusFilter] = useState("all");
  const [jobsLastUpdatedAt, setJobsLastUpdatedAt] = useState<string | null>(null);
  const [showHeaderRecent, setShowHeaderRecent] = useState(false);
  const [catalogComponents, setCatalogComponents] = useState<any[]>([]);
  const [serverStatus, setServerStatus] = useState<"connected" | "disconnected">("disconnected");
  const [selectedImage, setSelectedImage] = useState<string | null>(null);
  const [generateProductImage, setGenerateProductImage] = useState(false);
  const [mechElectricalActive, setMechElectricalActive] = useState(true);
  const [mechToggles, setMechToggles] = useState({
    structural: true,
    enclosure: true,
    mechanism: true,
    misc: false,
    print: true,
  });

  const fileInputRefSidebar = useRef<HTMLInputElement>(null);
  const fileInputRefCenter = useRef<HTMLInputElement>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  const goHome = () => {
    setProjectIR(null);
    setMermaidCode("");
    setSvgSchematic("");
    setActiveTab("overview");
    router.push("/");
  };

  const syncProjectRoute = (projectId: string, mode: "push" | "replace" = "push") => {
    const nextPath = projectRoute(projectId);
    if (window.location.pathname === nextPath) return;
    if (mode === "replace") {
      router.replace(nextPath);
    } else {
      router.push(nextPath);
    }
  };

  useEffect(() => {
    checkServerStatus();
    fetchCatalog();
    fetchProjectHistory();
  }, []);

  const checkServerStatus = async () => {
    try {
      const res = await fetch(`${API_URL}/`);
      setServerStatus(res.ok ? "connected" : "disconnected");
    } catch {
      setServerStatus("disconnected");
    }
  };

  const fetchCatalog = async () => {
    try {
      const res = await fetch(`${API_URL}/api/components`);
      if (res.ok) setCatalogComponents(await res.json());
    } catch (e) {
      console.error("Error fetching catalog", e);
    }
  };

  const fetchProjectHistory = async () => {
    try {
      const res = await fetch(`${API_URL}/api/projects`);
      if (res.ok) setProjectHistory(await res.json());
    } catch (e) {
      console.error("Error fetching project history", e);
    }
  };

  const fetchA2aJobs = useCallback(async (status: string, options: { silent?: boolean } = {}) => {
    if (!options.silent) setJobsLoading(true);
    setJobsError(null);
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (status !== "all") params.set("status", status);
      const res = await fetch(`${API_URL}/api/a2a/jobs?${params.toString()}`);
      if (!res.ok) throw new Error(`Jobs endpoint returned ${res.status}`);
      setA2aJobs(await res.json());
      setJobsLastUpdatedAt(new Date().toISOString());
    } catch (e) {
      console.error("Error fetching A2A jobs", e);
      setJobsError("Jobs are unavailable");
    } finally {
      if (!options.silent) setJobsLoading(false);
    }
  }, []);

  const changeJobStatusFilter = (status: string) => {
    setJobStatusFilter(status);
    fetchA2aJobs(status);
  };

  useEffect(() => {
    fetchA2aJobs(jobStatusFilter);

    const pollJobs = () => {
      if (document.visibilityState === "visible") {
        fetchA2aJobs(jobStatusFilter, { silent: true });
      }
    };

    const intervalId = window.setInterval(pollJobs, JOB_POLL_INTERVAL_MS);
    document.addEventListener("visibilitychange", pollJobs);

    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", pollJobs);
    };
  }, [fetchA2aJobs, jobStatusFilter]);

  const handleImageChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onloadend = () => setSelectedImage(reader.result as string);
    reader.readAsDataURL(file);
  };

  const removeSelectedImage = () => {
    setSelectedImage(null);
    if (fileInputRefSidebar.current) fileInputRefSidebar.current.value = "";
    if (fileInputRefCenter.current) fileInputRefCenter.current.value = "";
  };

  const buildReactFlowGraph = (ir: any) => {
    if (!ir?.components) return;

    const newNodes: Node[] = [];
    const newEdges: Edge[] = [];
    const electricalParts = ir.components.filter(
      (component: any) => !["mechanical", "3d print"].includes(component.category?.toLowerCase())
    );
    const electricalRefs = new Set(electricalParts.map((component: any) => component.ref_des));
    const componentByRef = new Map<string, any>(electricalParts.map((component: any) => [component.ref_des, component]));
    const pinMapByRef = new Map<string, Map<string, SchematicPin>>();

    electricalParts.forEach((component: any) => {
      const pinMap = new Map<string, SchematicPin>();
      (component.pins || []).forEach((pin: any) => {
        if (!pin?.pin_id) return;
        pinMap.set(pin.pin_id, {
          pin_id: pin.pin_id,
          name: pin.name,
          pin_type: pin.pin_type,
          voltage: pin.voltage,
        });
      });
      pinMapByRef.set(component.ref_des, pinMap);
    });

    (ir.nets || []).forEach((net: any) => {
      (net.pins || []).forEach((pinRef: any) => {
        if (!electricalRefs.has(pinRef.ref_des)) return;
        const pinMap = pinMapByRef.get(pinRef.ref_des);
        if (!pinMap || pinMap.has(pinRef.pin_id)) return;
        pinMap.set(pinRef.pin_id, {
          pin_id: pinRef.pin_id,
          name: pinRef.pin_id,
          pin_type: net.net_type,
          voltage: net.voltage,
        });
      });
    });

    const schematicMeta = ir.assembly_metadata?.schematic || {};
    const explicitPlacements = schematicMeta.placements || {};
    const defaultColumns: Record<string, { x: number; y: number }> = {
      power: { x: 360, y: 60 },
      sensor: { x: 620, y: 60 },
      microcontroller: { x: 620, y: 300 },
      passives: { x: 880, y: 120 },
      communication: { x: 880, y: 360 },
      actuator: { x: 1140, y: 120 },
      display: { x: 1140, y: 360 },
      default: { x: 880, y: 560 },
    };
    const categoryCounts: Record<string, number> = {};

    electricalParts.forEach((component: any) => {
      const category = component.category?.toLowerCase() || "default";
      const placement = normalizePlacement(explicitPlacements[component.ref_des]);
      const baseColumn = defaultColumns[category] || defaultColumns.default;
      const groupIndex = categoryCounts[category] || 0;
      categoryCounts[category] = groupIndex + 1;
      const position = placement || {
        x: baseColumn.x,
        y: baseColumn.y + groupIndex * 185,
      };
      const pins = Array.from(pinMapByRef.get(component.ref_des)?.values() || []).sort((a, b) =>
        pinKey(a).localeCompare(pinKey(b), undefined, { numeric: true })
      );

      newNodes.push({
        id: component.ref_des,
        type: "schematicPart",
        position,
        draggable: true,
        data: {
          component,
          pins,
          tone: schematicToneForCategory(category),
        },
        style: { background: "transparent", border: "none", width: 190 },
      });
    });

    const netStyles: Record<string, { color: string; dash?: string; width: number }> = {
      ground: { color: "#94a3b8", dash: "8 6", width: 2 },
      power: { color: "#f5a400", dash: "5 5", width: 2 },
      i2c: { color: "#22c55e", width: 2 },
      spi: { color: "#22c55e", width: 2 },
      digital: { color: "#22c55e", width: 2 },
      analog: { color: "#22c55e", width: 2 },
      pwm: { color: "#22c55e", width: 2 },
      default: { color: "#22c55e", width: 2 },
    };

    const pinTypeForRef = (pinRef: any) =>
      pinMapByRef.get(pinRef.ref_des)?.get(pinRef.pin_id)?.pin_type?.toLowerCase() || "";

    const chooseSourcePin = (net: any, usablePins: any[]) => {
      const netType = net.net_type?.toLowerCase() || "default";
      if (netType === "power" || netType === "ground") {
        return (
          usablePins.find((pinRef: any) => componentByRef.get(pinRef.ref_des)?.category?.toLowerCase() === "power") ||
          usablePins.find((pinRef: any) => pinTypeForRef(pinRef) === netType) ||
          usablePins[0]
        );
      }
      return (
        usablePins.find((pinRef: any) => componentByRef.get(pinRef.ref_des)?.category?.toLowerCase() === "microcontroller") ||
        usablePins[0]
      );
    };

    const edgeLabel = (net: any, sourcePin: any, targetPin: any) => {
      const voltage = typeof net.voltage === "number" ? `${net.voltage}V` : net.net_type || "net";
      return `${net.name || net.net_id} / ${voltage} / ${sourcePin.pin_id}->${targetPin.pin_id}`;
    };

    (ir.nets || []).forEach((net: any) => {
      const netType = net.net_type?.toLowerCase() || "default";
      const style = netStyles[netType] || netStyles.default;
      const usablePins = (net.pins || []).filter((pinRef: any) => electricalRefs.has(pinRef.ref_des));

      if (usablePins.length < 2) return;

      const sourcePin = chooseSourcePin(net, usablePins);
      usablePins
        .filter((targetPin: any) => targetPin !== sourcePin)
        .forEach((targetPin: any, index: number) => {
          const id = `edge_${net.net_id}_${sourcePin.ref_des}_${sourcePin.pin_id}_to_${targetPin.ref_des}_${targetPin.pin_id}_${index}`;

          newEdges.push({
            id,
            source: sourcePin.ref_des,
            sourceHandle: schematicHandleId(sourcePin.ref_des, sourcePin.pin_id),
            target: targetPin.ref_des,
            targetHandle: schematicHandleId(targetPin.ref_des, targetPin.pin_id),
            type: "smoothstep",
            animated: false,
            label: edgeLabel(net, sourcePin, targetPin),
            labelBgPadding: [4, 2],
            labelBgBorderRadius: 2,
            labelBgStyle: { fill: "#ffffff", fillOpacity: 0.88 },
            labelStyle: { fill: style.color, fontWeight: 800, fontSize: 9, fontFamily: "monospace" },
            style: {
              stroke: style.color,
              strokeWidth: style.width,
              strokeDasharray: style.dash || "none",
            },
            markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12, color: style.color },
          });
        });
    });

    setNodes(newNodes);
    setEdges(newEdges);
  };

  const handleGenerate = async (event: React.FormEvent) => {
    event.preventDefault();
    const promptText = prompt.trim() || "Infer a buildable hardware project from the uploaded reference image.";
    if (!prompt.trim() && !selectedImage) return;

    const imageData = selectedImage;
    setIsLoading(true);
    checkServerStatus();

    try {
      const res = await fetch(`${API_URL}/api/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: promptText,
          image_data: imageData || null,
          generate_image: generateProductImage,
        }),
      });

      if (!res.ok) throw new Error("Compilation server failed");

      const data = await res.json();
      const ir = withProjectResponseMetadata(data.project_ir, data);
      setProjectIR(ir);
      setMermaidCode(data.mermaid_code);
      setSvgSchematic(data.svg_schematic);
      buildReactFlowGraph(ir);
      const projectId = projectIdFromIR(ir);
      if (projectId) syncProjectRoute(projectId);
      fetchProjectHistory();
      fetchA2aJobs(jobStatusFilter, { silent: true });
    } catch (error) {
      console.warn("Using local simulation fallback", error);
      const mockRes = await runMockCompilation(promptText, imageData);
      setProjectIR(mockRes.project_ir);
      setMermaidCode(mockRes.mermaid_code);
      setSvgSchematic(mockRes.svg_schematic);
      buildReactFlowGraph(mockRes.project_ir);
    } finally {
      setSelectedImage(null);
      setActiveTab("overview");
      setIsLoading(false);
    }
  };

  const loadExample = async (filename: string) => {
    setIsLoading(true);
    try {
      const res = await fetch(`/examples/${filename}`);
      if (!res.ok) return;

      const ir = await res.json();
      setProjectIR(ir);
      setMermaidCode(pipelineMermaidCode);
      setSvgSchematic(generateMockSvg(ir));
      buildReactFlowGraph(ir);
      setActiveTab("overview");
    } catch (error) {
      console.error("Error loading example", error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const example = params.get("example");
    const tab = normalizeTab(params.get("tab"));
    if (!example) {
      if (tab) setActiveTab(tab);
      return;
    }

    const filename = example.endsWith(".json") ? example : `${example}.json`;
    loadExample(filename).then(() => {
      if (tab) setActiveTab(tab);
    });
  }, []);

  const generateMockSvg = (ir: any): string => {
    const components = ir.components || [];
    const controller =
      components.find((component: any) => component.category?.toLowerCase() === "microcontroller") || components[0];
    const inputs = components
      .filter((component: any) => ["sensor", "power"].includes(component.category?.toLowerCase()))
      .slice(0, 2);
    const outputs = components
      .filter((component: any) => ["actuator", "display", "passives"].includes(component.category?.toLowerCase()))
      .slice(0, 3);

    return `<svg viewBox="0 0 880 420" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">
      <rect width="880" height="420" fill="#141519"/>
      <g stroke="#2a2d35" stroke-width="1">
        <path d="M40 80 H840"/><path d="M40 180 H840"/><path d="M40 280 H840"/>
        <path d="M180 40 V380"/><path d="M440 40 V380"/><path d="M700 40 V380"/>
      </g>
      <text x="44" y="42" font-family="monospace" font-size="14" font-weight="700" fill="#ffffff">BLUEPRINT WIRING DIAGRAM</text>
      <text x="44" y="64" font-family="monospace" font-size="11" fill="#8b8e99">Generated from validated Hardware IR</text>
      <rect x="330" y="116" width="220" height="188" fill="#111216" stroke="#22d3ee" stroke-width="2"/>
      <text x="440" y="154" font-family="monospace" font-size="15" font-weight="700" fill="#ffffff" text-anchor="middle">MAIN CONTROLLER</text>
      <text x="440" y="177" font-family="monospace" font-size="12" fill="#22d3ee" text-anchor="middle">${controller?.part_number || "Controller"}</text>
      <rect x="60" y="92" width="170" height="86" fill="#111216" stroke="#34d399" stroke-width="1.5"/>
      <text x="145" y="127" font-family="monospace" font-size="12" font-weight="700" fill="#ffffff" text-anchor="middle">INPUT</text>
      <text x="145" y="149" font-family="monospace" font-size="11" fill="#34d399" text-anchor="middle">${(inputs[0]?.name || "Sensor input").slice(0, 23)}</text>
      <path d="M230 135 C270 135 286 162 330 164" fill="none" stroke="#34d399" stroke-width="2"/>
      <rect x="60" y="228" width="170" height="86" fill="#111216" stroke="#facc15" stroke-width="1.5"/>
      <text x="145" y="263" font-family="monospace" font-size="12" font-weight="700" fill="#ffffff" text-anchor="middle">POWER</text>
      <text x="145" y="285" font-family="monospace" font-size="11" fill="#facc15" text-anchor="middle">${(inputs[1]?.name || "Power rail").slice(0, 23)}</text>
      <path d="M230 271 H330" fill="none" stroke="#facc15" stroke-width="2" stroke-dasharray="7 7"/>
      <rect x="650" y="76" width="170" height="86" fill="#111216" stroke="#a78bfa" stroke-width="1.5"/>
      <text x="735" y="111" font-family="monospace" font-size="12" font-weight="700" fill="#ffffff" text-anchor="middle">OUTPUT</text>
      <text x="735" y="133" font-family="monospace" font-size="11" fill="#a78bfa" text-anchor="middle">${(outputs[0]?.name || "Output module").slice(0, 23)}</text>
      <path d="M550 166 C596 150 605 120 650 119" fill="none" stroke="#a78bfa" stroke-width="2"/>
      <rect x="650" y="196" width="170" height="86" fill="#111216" stroke="#ec4899" stroke-width="1.5"/>
      <text x="735" y="231" font-family="monospace" font-size="12" font-weight="700" fill="#ffffff" text-anchor="middle">MODULE</text>
      <text x="735" y="253" font-family="monospace" font-size="11" fill="#ec4899" text-anchor="middle">${(outputs[1]?.name || "Display").slice(0, 23)}</text>
      <path d="M550 231 H650" fill="none" stroke="#ec4899" stroke-width="2"/>
    </svg>`;
  };

  const runMockCompilation = async (userPrompt: string, imageData?: string | null): Promise<any> => {
    const promptLower = userPrompt.toLowerCase();
    let file = "biometric_deadbolt.json";

    if (
      imageData ||
      promptLower.includes("mp3") ||
      promptLower.includes("audio") ||
      promptLower.includes("music") ||
      promptLower.includes("player") ||
      promptLower.includes("pocket")
    ) {
      file = "pocket_mp3_player.json";
    } else if (promptLower.includes("water") || promptLower.includes("plant") || promptLower.includes("soil") || promptLower.includes("garden")) {
      file = "plant_watering.json";
    } else if (promptLower.includes("thermostat") || promptLower.includes("temperature") || promptLower.includes("weather")) {
      file = "smart_thermostat.json";
    }

    const res = await fetch(`/examples/${file}`);
    const ir = await res.json();
    ir.assembly_metadata = {
      ...(ir.assembly_metadata || {}),
      reference_image_data: imageData || ir.assembly_metadata?.reference_image_data || null,
      input_mode: imageData ? "prompt_image" : "prompt",
      image_features: ir.assembly_metadata?.image_features || ir.constraints || [],
    };
    return {
      project_ir: ir,
      mermaid_code: pipelineMermaidCode,
      svg_schematic: generateMockSvg(ir),
    };
  };

  const loadOldProject = async (projectId: string, options: { syncRoute?: boolean } = {}) => {
    const shouldSyncRoute = options.syncRoute ?? true;
    setIsLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/projects/${projectId}`);
      if (!res.ok) return;

      const data = await res.json();
      const ir = withProjectResponseMetadata(data.project_ir, data);
      setProjectIR(ir);
      setMermaidCode(data.mermaid_code);
      setSvgSchematic(data.svg_schematic);
      buildReactFlowGraph(ir);
      setActiveTab("overview");
      if (shouldSyncRoute) syncProjectRoute(projectId);
    } catch (error) {
      console.error(error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (!routeProjectId) return;

    const tab = normalizeTab(new URLSearchParams(window.location.search).get("tab"));
    loadOldProject(decodeURIComponent(routeProjectId), { syncRoute: false }).then(() => {
      if (tab) setActiveTab(tab);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeProjectId]);

  const findProjectForJob = (job: A2AJob) => {
    const projectId = job.result_summary?.project_id;
    if (projectId) {
      const directMatch = projectHistory.find((project: any) => project.project_id === projectId);
      return directMatch || { project_id: projectId };
    }

    const prompt = job.payload?.prompt;
    const title = job.result_summary?.title;
    if (!prompt && !title) return null;

    return projectHistory.find((project: any) => {
      const promptMatches = prompt ? project.prompt === prompt : true;
      const titleMatches = title ? project.title === title : true;
      return promptMatches && titleMatches;
    }) || null;
  };

  const loadProjectForJob = async (job: A2AJob) => {
    const project = findProjectForJob(job);
    if (!project?.project_id) return;
    await loadOldProject(project.project_id);
  };

  const downloadJSONIR = () => {
    if (!projectIR) return;
    const jsonStr = JSON.stringify(projectIR, null, 2);
    const blob = new Blob([jsonStr], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const title = projectIR.overview?.title || "blueprint_project";
    link.href = url;
    link.download = `${title.toLowerCase().replace(/\s+/g, "_")}_blueprint.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const getOverviewMetrics = () => {
    if (!projectIR?.components) {
      return { electricalParts: 0, mechanicalParts: 0, totalParts: 0, electricalCost: 0, mechanicalCost: 0, totalCost: 0 };
    }

    let electricalParts = 0;
    let mechanicalParts = 0;
    let electricalCost = 0;
    let mechanicalCost = 0;

    projectIR.components.forEach((component: any) => {
      const category = component.category?.toLowerCase() || "";
      const quantity = component.quantity || 1;
      const unitPrice = component.unit_price || 0;

      if (["mechanical", "3d print"].includes(category)) {
        mechanicalParts += quantity;
        mechanicalCost += unitPrice * quantity;
      } else {
        electricalParts += quantity;
        electricalCost += unitPrice * quantity;
      }
    });

    return {
      electricalParts,
      mechanicalParts,
      totalParts: electricalParts + mechanicalParts,
      electricalCost: Number(electricalCost.toFixed(2)),
      mechanicalCost: Number(mechanicalCost.toFixed(2)),
      totalCost: Number((electricalCost + mechanicalCost).toFixed(2)),
    };
  };

  const metrics = getOverviewMetrics();
  const components = projectIR?.components || [];
  const assembly = projectIR?.assembly || [];
  const constraints = projectIR?.constraints || [];
  const imageFeatures = projectIR?.assembly_metadata?.image_features?.length
    ? projectIR.assembly_metadata.image_features
    : constraints;
  const issues = [
    ...(projectIR?.validation?.critical || []),
    ...(projectIR?.validation?.warning || []),
    ...(projectIR?.validation?.info || []),
    ...(projectIR?.validation_issues || []),
  ];
  const projectTitle = projectIR?.overview?.title || "Untitled Hardware Project";
  const projectDescription = projectIR?.overview?.description || "Generated hardware package";
  const projectImage =
    projectIR?.assembly_metadata?.product_image_data ||
    projectIR?.assembly_metadata?.product_image_url ||
    projectIR?.assembly_metadata?.reference_image_data ||
    null;
  const projectImageLabel = projectIR?.assembly_metadata?.product_image_data || projectIR?.assembly_metadata?.product_image_url
    ? `Generated by ${projectIR?.assembly_metadata?.product_image_model || projectIR?.assembly_metadata?.image_output_model || "image model"}`
    : "Uploaded hardware reference";
  const currentProjectId = projectIR?.assembly_metadata?.project_id || null;
  const currentProjectJobId = projectIR?.assembly_metadata?.frontend_job_id || null;
  const projectJobs = a2aJobs.filter((job) => {
    if (currentProjectJobId && job.job_id === currentProjectJobId) return true;
    if (currentProjectId && job.result_summary?.project_id === currentProjectId) return true;
    return false;
  });

  if (!projectIR) {
    return (
      <div className="min-h-screen bg-[#141519] font-sans text-slate-100">
        <header className="border-b border-[#292b31] bg-[#141519]/95">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-4">
            <button type="button" onClick={goHome} className="text-left">
              <span className="flex items-center gap-3">
                <span className="flex h-9 w-9 items-center justify-center border border-[#2c2f37] bg-black text-white">
                  <Cpu className="h-4 w-4" />
                </span>
                <span className="block text-sm font-black uppercase tracking-[0.22em] text-white">Blueprint</span>
              </span>
            </button>
            <div className="flex items-center gap-2">
              <span className={`hidden border px-3 py-1.5 text-xs font-semibold sm:block ${
                serverStatus === "connected"
                  ? "border-emerald-500/30 bg-emerald-950/30 text-emerald-400"
                  : "border-amber-500/30 bg-amber-950/30 text-amber-400"
              }`}>
                {serverStatus === "connected" ? "API connected" : "Demo mode"}
              </span>
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setShowHeaderRecent((s) => !s)}
                  className="inline-flex items-center gap-2 border border-[#2c2f37] px-4 py-2 text-xs font-semibold text-slate-300 hover:bg-white hover:text-black"
                >
                  <History className="h-4 w-4 text-slate-300" />
                  Recent projects
                </button>

                {showHeaderRecent && (
                  <div className="absolute right-0 mt-2 w-64 rounded border border-[#2c2f37] bg-[#17181d] p-2 text-sm">
                    {projectHistory.length > 0 ? (
                      <div className="space-y-1">
                        {projectHistory.slice(0, 4).map((proj: any) => (
                          <button
                            key={proj.project_id}
                            type="button"
                            onClick={() => {
                              setShowHeaderRecent(false);
                              loadOldProject(proj.project_id);
                            }}
                            className="w-full text-left px-2 py-2 hover:bg-black/30"
                          >
                            <div className="truncate font-semibold text-white">{proj.title || proj.prompt || "Untitled"}</div>
                            <div className="truncate text-xs text-slate-500">{(proj.prompt || "").slice(0, 60)}</div>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <div className="text-xs text-slate-500 p-2">No recent projects</div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-6xl px-5 py-12">
            <section className="mx-auto max-w-3xl text-center">
            <p className="text-sm font-medium text-slate-500">Shack 15</p>
            <h1 className="mt-4 text-4xl font-semibold leading-tight text-white sm:text-6xl">
              Turn an idea into a hardware plan.
            </h1>
            <p className="mx-auto mt-5 max-w-2xl text-base leading-7 text-slate-400">
              Upload a photo, sketch, or short description. Get parts, wiring, cost, and build steps.
            </p>

            <form onSubmit={handleGenerate} className="mt-8 border border-[#2c2f37] bg-[#17181d] p-3 text-left shadow-2xl shadow-black/30">
              <div className="relative">
                <textarea
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  placeholder="Describe what you want to build, or upload an image."
                  className="min-h-[138px] w-full resize-none bg-transparent p-4 pr-16 pb-16 text-sm leading-7 text-slate-100 outline-none placeholder:text-slate-600"
                />
                <button
                  type="submit"
                  disabled={isLoading || (!prompt.trim() && !selectedImage)}
                  className="absolute bottom-4 right-4 inline-flex h-10 w-10 items-center justify-center bg-white text-black transition hover:bg-slate-200 disabled:cursor-not-allowed disabled:opacity-40"
                  aria-label="Compile hardware"
                  title="Compile hardware"
                >
                  {isLoading ? <RefreshCw className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
                </button>
              </div>
              {selectedImage && (
                <div className="mb-3 flex items-center gap-3 border border-[#2c2f37] bg-black/30 p-2">
                  <img src={selectedImage} alt="Attached reference" className="h-16 w-24 object-cover" />
                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-semibold text-white">Image added</div>
                    <div className="mt-1 text-[11px] text-slate-500">Blueprint will use this image to understand the design.</div>
                  </div>
                  <button type="button" onClick={removeSelectedImage} className="p-2 text-slate-500 hover:text-white" aria-label="Remove image">
                    <X className="h-4 w-4" />
                  </button>
                </div>
              )}
              <div className="flex flex-col gap-3 border-t border-[#2c2f37] px-2 pt-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-2">
                  <input ref={fileInputRefCenter} type="file" accept="image/*" onChange={handleImageChange} className="hidden" />
                  <button
                    type="button"
                    onClick={() => fileInputRefCenter.current?.click()}
                    className="inline-flex h-10 w-10 items-center justify-center border border-[#2c2f37] text-slate-400 hover:bg-white hover:text-black"
                    title="Add image"
                  >
                    <Paperclip className="h-4 w-4" />
                  </button>
                  <label className="inline-flex h-10 cursor-pointer items-center gap-2 border border-[#2c2f37] px-3 text-xs font-black uppercase text-slate-400 hover:border-slate-500 hover:text-white">
                    <input
                      type="checkbox"
                      checked={generateProductImage}
                      onChange={(event) => setGenerateProductImage(event.target.checked)}
                      className="peer sr-only"
                    />
                    <Sparkles className={`h-4 w-4 ${generateProductImage ? "text-cyan-300" : "text-slate-500"}`} />
                    <span>Image model</span>
                    <span className={`h-4 w-7 border transition ${generateProductImage ? "border-cyan-300 bg-cyan-300" : "border-[#3a3d46] bg-black"}`}>
                      <span className={`block h-full w-3.5 bg-white transition ${generateProductImage ? "translate-x-3" : "translate-x-0"}`} />
                    </span>
                  </label>
                </div>
              </div>
            </form>

            <div className="mt-5 flex flex-wrap justify-center gap-2">
              {samplePrompts.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => setPrompt(example)}
                  className="border border-[#2c2f37] bg-[#17181d] px-3 py-2 text-[11px] leading-5 text-slate-400 hover:border-slate-500 hover:text-white"
                >
                  {example}
                </button>
              ))}
            </div>
          </section>

          <section className="mt-12 grid gap-4 lg:grid-cols-[1.35fr_0.85fr]">
            <div className="border border-[#2c2f37] bg-[#17181d] p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-base font-semibold text-white">Examples</h2>
                  <p className="mt-1 text-xs text-slate-500">Open a finished hardware plan.</p>
                </div>
                <span className="text-xs text-slate-500">More</span>
              </div>
              <div className="grid gap-3 md:grid-cols-3">
                {communityProjects.map((project) => (
                  <button
                    key={project.file}
                    type="button"
                    onClick={() => loadExample(project.file)}
                    className="group border border-[#2c2f37] bg-[#141519] p-4 text-left hover:border-slate-500"
                  >
                    <div className="mb-4 flex h-10 w-10 items-center justify-center bg-black text-white">
                      <Sparkles className="h-4 w-4" />
                    </div>
                    <h3 className="text-sm font-semibold text-white">{project.title}</h3>
                    <p className="mt-2 text-xs leading-6 text-slate-500">{project.description}</p>
                    <span className="mt-4 inline-flex items-center gap-1 text-xs font-semibold text-white">
                      Open project
                      <ArrowRight className="h-4 w-4 transition group-hover:translate-x-0.5" />
                    </span>
                  </button>
                ))}
              </div>
            </div>

            <JobsPanel
              jobs={a2aJobs}
              loading={jobsLoading}
              error={jobsError}
              statusFilter={jobStatusFilter}
              onStatusFilterChange={changeJobStatusFilter}
              onRefresh={() => fetchA2aJobs(jobStatusFilter)}
              onOpenProject={loadProjectForJob}
              findProjectForJob={findProjectForJob}
              lastUpdatedAt={jobsLastUpdatedAt}
              pollIntervalMs={JOB_POLL_INTERVAL_MS}
              compact
            />
          </section>
        </main>
      </div>
    );
  }

  return (
    <div className="h-screen overflow-hidden bg-[#141519] text-slate-200">
      <div className="grid h-full min-h-0 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_280px]">
        <main className="flex min-h-0 min-w-0 flex-col">
          <header className="relative flex min-h-[78px] items-center justify-center border-b border-[#282a30] bg-[#17181d] px-4">
            <button
              type="button"
              onClick={goHome}
              aria-label="Back to overview"
              className="absolute left-4 hidden items-center gap-3 text-left md:flex"
            >
              <span className="flex h-9 w-9 items-center justify-center border border-[#30323a] bg-black text-white">
                <Cpu className="h-4 w-4" />
              </span>
            </button>

            <nav className="flex overflow-x-auto border border-[#2a2c33]">
              {workspaceTabs.map((tab) => {
                const Icon = tab.icon;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setActiveTab(tab.id)}
                    className={`inline-flex h-11 min-w-12 items-center justify-center gap-2 border-r border-[#2a2c33] px-4 text-xs font-black uppercase tracking-widest transition last:border-r-0 ${
                      activeTab === tab.id ? "bg-white text-black" : "bg-[#17181d] text-slate-500 hover:text-white"
                    }`}
                  >
                    <Icon className="h-4 w-4" />
                    <span className={activeTab === tab.id ? "inline" : "hidden sm:inline"}>{tab.label}</span>
                  </button>
                );
              })}
            </nav>

          </header>

          <section className="min-h-0 flex-1 overflow-hidden">
            {activeTab === "overview" && (
              <OverviewPanel
                title={projectTitle}
                description={projectDescription}
                image={projectImage}
                imageLabel={projectImageLabel}
                features={imageFeatures}
                metrics={metrics}
                metadata={projectIR.assembly_metadata || {}}
              />
            )}

            {activeTab === "bom" && (
              <BomPanel
                components={components}
                metrics={metrics}
                cadSources={(projectIR.mechanical && Array.isArray(projectIR.mechanical.cad_sources)) ? projectIR.mechanical.cad_sources : []}
                fabricationCost={Number(projectIR.mechanical?.fabrication_cost_estimate_usd || 0)}
              />
            )}

            {activeTab === "mechanical" && (
              <MechanicalPanel
                toggles={mechToggles}
                setToggles={setMechToggles}
                electricalActive={mechElectricalActive}
                setElectricalActive={setMechElectricalActive}
                components={components}
                features={imageFeatures}
                metadata={projectIR.assembly_metadata || {}}
                mechanical={projectIR.mechanical || {}}
              />
            )}

            {activeTab === "schematic" && (
              <div className="h-full min-h-[560px] bg-[#f7f7f5]">
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  nodeTypes={schematicNodeTypes}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  fitView
                  fitViewOptions={{ padding: 0.34 }}
                  className="bg-[#f7f7f5]"
                >
                  <Background color="#e7e9ef" gap={22} size={1} />
                  <Controls className="!border !border-[#d9dce3] !bg-white !text-[#202127]" />
                  <MiniMap className="!border !border-[#d9dce3] !bg-white" nodeStrokeColor="#94a3b8" nodeColor="#f8fafc" maskColor="rgba(255,255,255,0.62)" />
                  <SchematicLegend />
                </ReactFlow>
              </div>
            )}

            {activeTab === "assembly" && (
              <AssemblyPanel assembly={assembly} issues={issues} onDownload={downloadJSONIR} />
            )}

            {activeTab === "svg" && (
              <div className="h-full overflow-auto bg-[#141519] p-6">
                <div className="mx-auto max-w-5xl border border-[#2a2c33] bg-[#17181d] p-5" dangerouslySetInnerHTML={{ __html: svgSchematic }} />
              </div>
            )}

            {activeTab === "jobs" && (
              <JobsPanel
                jobs={projectJobs}
                loading={jobsLoading}
                error={jobsError}
                statusFilter={jobStatusFilter}
                onStatusFilterChange={changeJobStatusFilter}
                onRefresh={() => fetchA2aJobs(jobStatusFilter)}
                onOpenProject={loadProjectForJob}
                findProjectForJob={findProjectForJob}
                lastUpdatedAt={jobsLastUpdatedAt}
                pollIntervalMs={JOB_POLL_INTERVAL_MS}
                title="Project Jobs"
                description="Only jobs tied to this project are shown here."
                emptyMessage="No jobs recorded for this project and filter."
              />
            )}
          </section>
        </main>

        <PartsSidebar components={components} issues={issues} isValid={projectIR.is_valid} />
      </div>
    </div>
  );
}

export default BlueprintWorkspace;

function SchematicLegend() {
  const nodeRows = [
    ["MCU", schematicTones.microcontroller],
    ["SENSOR", schematicTones.sensor],
    ["ACTUATOR", schematicTones.actuator],
    ["POWER", schematicTones.power],
    ["MODULE", schematicTones.passives],
    ["DISPLAY", schematicTones.display],
  ] as const;

  const wireRows = [
    { label: "DATA", color: "#22c55e", dash: "none" },
    { label: "POWER", color: "#f5a400", dash: "5 5" },
    { label: "GROUND", color: "#94a3b8", dash: "8 6" },
  ];

  return (
    <div className="pointer-events-none absolute bottom-5 left-5 z-10 w-[184px] border border-[#d9dce3] bg-white px-6 py-6 shadow-[0_18px_45px_rgba(15,23,42,0.14)]">
      <div className="text-[21px] font-black uppercase tracking-[0.2em] text-[#202127]">Schematic</div>
      <div className="mt-4 border-t border-[#d9dce3] pt-4 text-[12px] font-black uppercase tracking-[0.2em] text-[#777b86]">Node Types</div>
      <div className="mt-4 space-y-3">
        {nodeRows.map(([label, tone]) => (
          <div key={label} className="flex items-center gap-3 text-[18px] font-black uppercase tracking-[0.08em]" style={{ color: tone.text }}>
            <Eye className="h-4 w-4" />
            <span>{label}</span>
          </div>
        ))}
      </div>
      <div className="mt-5 border-t border-[#d9dce3] pt-4 space-y-3">
        {wireRows.map((wire) => (
          <div key={wire.label} className="flex items-center gap-3 text-[18px] font-black uppercase tracking-[0.08em]" style={{ color: wire.color }}>
            <Eye className="h-4 w-4" />
            <svg width="40" height="8" viewBox="0 0 40 8" aria-hidden="true">
              <line x1="0" y1="4" x2="40" y2="4" stroke={wire.color} strokeWidth="3" strokeDasharray={wire.dash} />
            </svg>
            <span>{wire.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function OverviewPanel({
  title,
  description,
  image,
  imageLabel,
  features,
  metrics,
  metadata,
}: {
  title: string;
  description: string;
  image: string | null;
  imageLabel: string;
  features: string[];
  metrics: ReturnType<typeof emptyMetrics>;
  metadata: Record<string, any>;
}) {
  return (
    <div className="h-full overflow-y-auto bg-[#141519] px-5 py-8">
      <div className="mx-auto max-w-[890px]">
        <div className="relative border border-[#2a2c33] bg-[#d5d5d3]">
          {image ? (
            <img src={image} alt={imageLabel} className="h-[440px] w-full object-contain" />
          ) : (
            <ProductRender product={metadata.product_visual} />
          )}
          <button className="absolute right-4 top-4 flex h-10 w-10 items-center justify-center rounded-full bg-white/90 text-blue-600 shadow-lg" title={image ? imageLabel : "Generated visual reference"}>
            <Eye className="h-5 w-5" />
          </button>
        </div>

        <div className="mt-6 border-t border-[#282a30] px-8 py-8">
          <h1 className="text-2xl font-black uppercase tracking-[0.18em] text-white">{title}</h1>
          <div className="mt-5 flex flex-wrap gap-2">
            {features.slice(0, 12).map((feature, index) => (
              <span key={`${feature}-${index}`} className="border border-[#333640] px-3 py-1.5 text-[11px] font-black uppercase tracking-[0.16em] text-slate-400">
                {String(feature).split(":")[0]}
              </span>
            ))}
          </div>

          <div className="mt-7">
            <div className="text-[11px] font-black uppercase tracking-[0.22em] text-slate-500">Technical Description</div>
            <p className="mt-4 max-w-3xl text-base leading-8 text-slate-300">{description}</p>
          </div>

          <div className="mt-7 max-w-2xl border border-[#2a2c33]">
            <div className="grid grid-cols-3 border-b border-[#2a2c33] px-4 py-3 text-[12px] font-black uppercase tracking-[0.18em] text-slate-500">
              <span>Category</span>
              <span className="text-center">Parts</span>
              <span className="text-right">Cost</span>
            </div>
            <SummaryRow label="Electrical" parts={metrics.electricalParts} cost={metrics.electricalCost} />
            <SummaryRow label="Mechanical" parts={metrics.mechanicalParts} cost={metrics.mechanicalCost} />
            <SummaryRow label="Total" parts={metrics.totalParts} cost={metrics.totalCost} strong />
          </div>
        </div>
      </div>
    </div>
  );
}

function BomPanel({ components, metrics, cadSources = [], fabricationCost = 0 }: { components: any[]; metrics: ReturnType<typeof emptyMetrics>; cadSources?: any[]; fabricationCost?: number }) {
  return (
    <div className="h-full overflow-y-auto bg-[#141519] p-5">
      <div className="border border-[#2a2c33]">
        <div className="grid min-w-[980px] grid-cols-[minmax(420px,1fr)_110px_110px_150px_140px] border-b border-[#f5f5f5] px-5 py-5 text-sm font-black uppercase tracking-widest text-white">
          <span>Part</span>
          <span className="text-center">Qty</span>
          <span>Unit</span>
          <span>Source</span>
          <span className="text-right">Subtotal</span>
        </div>
        <div className="min-w-[980px] divide-y divide-[#282a30]">
          {components.map((component) => (
            <div key={component.ref_des} className="grid grid-cols-[minmax(420px,1fr)_110px_110px_150px_140px] items-center px-5 py-6">
              <div className="flex items-start gap-4">
                <PartThumb component={component} />
                <div className="min-w-0">
                  <h3 className="text-lg font-black text-white">{component.name}</h3>
                  <div className="mt-2 text-sm text-slate-500">{component.category}</div>
                  <p className="mt-3 max-w-xl text-sm leading-6 text-slate-500">{component.rationale}</p>
                  <CategoryBadge category={component.category} />
                </div>
              </div>
              <div className="text-center text-base text-slate-200">{component.quantity}</div>
              <div className="text-base text-slate-200">~${Number(component.unit_price || 0).toFixed(2)}</div>
              <div className="flex flex-col items-start gap-2">
                {getSourcesForComponent(component).map((source) => (
                  <span key={source.label} className={`${source.className} inline-flex min-w-[86px] justify-center px-3 py-2 text-xs font-black italic text-black`}>
                    {source.label}
                  </span>
                ))}
              </div>
              <div className="text-right text-lg font-black text-white">~${((component.unit_price || 0) * (component.quantity || 1)).toFixed(2)}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-5 flex items-center justify-between border border-[#2a2c33] px-6 py-6">
        <span className="text-sm font-black uppercase tracking-[0.22em] text-slate-400">Total Estimated Cost</span>
        <span className="text-3xl font-black text-white">~${metrics.totalCost.toFixed(2)}</span>
      </div>

      <div className="mt-5 border border-[#2a2c33] p-4">
        <div className="flex items-start justify-between gap-4 border-b border-[#333640] pb-3">
          <div>
            <h2 className="text-sm font-black uppercase tracking-widest text-white">CAD Sources</h2>
            <div className="mt-2 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">3D Printed</div>
          </div>
          <div className="text-right">
            <div className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">Mech Cost</div>
            <div className="mt-1 text-lg font-black text-white">~${fabricationCost.toFixed(2)}</div>
          </div>
        </div>

        <div className="mt-4 space-y-3">
          {cadSources.length ? cadSources.slice(0, 3).map((source: any) => (
            <a
              key={`${source.name}-${source.url}`}
              href={source.url}
              target="_blank"
              rel="noreferrer"
              className="block border border-[#2a2c33] bg-[#141519] p-3 hover:border-cyan-400/60"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-xs font-black uppercase tracking-[0.14em] text-white">{source.name}</div>
                  <div className="mt-2 text-[10px] font-black uppercase tracking-[0.16em] text-cyan-300">{source.source_type || "CAD"} / ${(Number(source.estimated_unit_price_usd || 0)).toFixed(2)}</div>
                </div>
                <ExternalLink className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" />
              </div>
              {source.file_formats?.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1">
                  {source.file_formats.map((format: string) => (
                    <span key={format} className="border border-[#333640] px-2 py-1 text-[10px] font-black uppercase text-slate-500">{format}</span>
                  ))}
                </div>
              )}
            </a>
          )) : (
            <div className="border border-[#2a2c33] bg-[#141519] p-3 text-xs leading-6 text-slate-500">
              No CAD source records attached.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function MechanicalPanel({
  toggles,
  setToggles,
  electricalActive,
  setElectricalActive,
  components,
  features,
  metadata,
  mechanical,
}: {
  toggles: Record<string, boolean>;
  setToggles: (value: any) => void;
  electricalActive: boolean;
  setElectricalActive: (value: boolean) => void;
  components: any[];
  features: string[];
  metadata: Record<string, any>;
  mechanical: Record<string, any>;
}) {
  const dimensions = mechanical.render_dimensions || metadata.render_dimensions || { x_mm: 100, y_mm: 60, z_mm: 36 };
  const placements = mechanical.component_placements || metadata.component_placements || [];
  const relationships = mechanical.spatial_relationships || metadata.spatial_relationships || [];
  const cadSources = Array.isArray(mechanical.cad_sources) ? mechanical.cad_sources : [];
  const fabricationCost = Number(mechanical.fabrication_cost_estimate_usd || 0);

  return (
    <div className="relative h-full overflow-hidden bg-[#141519]">
      <div className="absolute inset-0 opacity-90" style={{
        backgroundImage:
          "linear-gradient(#252933 1px, transparent 1px), linear-gradient(90deg, #252933 1px, transparent 1px)",
        backgroundSize: "44px 44px",
        transform: "perspective(760px) rotateX(62deg) translateY(130px)",
        transformOrigin: "center 65%",
      }} />

      <div className="absolute left-6 top-1/2 z-20 w-36 -translate-y-1/2 border border-[#4b4d56] bg-[#17181d]/80 p-4">
        <h2 className="border-b border-slate-400 pb-3 text-sm font-black uppercase tracking-widest text-white">3D CAD</h2>
        <button
          type="button"
          onClick={() => setElectricalActive(!electricalActive)}
          className={`mt-3 flex w-full items-center gap-2 border-b border-slate-500 pb-3 text-left text-xs font-black uppercase ${
            electricalActive ? "text-cyan-400" : "text-slate-700"
          }`}
        >
          <Cpu className="h-3 w-3" />
          Electrical
        </button>
        <div className="mt-3 text-[10px] font-black uppercase tracking-widest text-slate-500">Mechanical</div>
        <div className="mt-2 space-y-2">
          {Object.entries(toggles).map(([key, value]) => (
            <button
              key={key}
              type="button"
              onClick={() => setToggles({ ...toggles, [key]: !value })}
              className={`flex items-center gap-2 text-xs font-black uppercase ${
                value ? layerColor(key) : "text-slate-700"
              }`}
            >
              <Eye className="h-3 w-3" />
              {key === "print" ? "3D Print" : key}
            </button>
          ))}
        </div>
      </div>

      

      <div className="relative z-10 flex h-full items-center justify-center px-8">
        <div className="relative h-[610px] w-[900px] max-w-full">
          <MechanicalScene
            dimensions={dimensions}
            components={components}
            placements={placements}
            relationships={relationships}
            features={features}
            toggles={toggles}
            electricalActive={electricalActive}
          />
        </div>
      </div>
    </div>
  );
}

function AssemblyPanel({ assembly, issues, onDownload }: { assembly: any[]; issues: any[]; onDownload: () => void }) {
  return (
    <div className="h-full overflow-y-auto bg-[#141519] p-6">
      <div className="mb-6 flex items-center justify-between border-b border-[#2a2c33] pb-5">
        <div>
          <h2 className="text-xl font-black uppercase tracking-[0.18em] text-white">Build Instructions</h2>
          <p className="mt-2 text-xs text-slate-500">Sequential assembly from the generated hardware graph.</p>
        </div>
        <button onClick={onDownload} className="flex items-center gap-2 border border-[#2a2c33] px-4 py-3 text-xs font-black uppercase tracking-widest text-white hover:bg-white hover:text-black">
          <Download className="h-4 w-4" />
          Export
        </button>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <div className="space-y-4">
          {assembly.map((step) => (
            <section key={step.step_num} className="border border-[#2a2c33] bg-[#17181d] p-5">
              <div className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center bg-white text-sm font-black text-black">
                  {step.step_num}
                </span>
                <div className="min-w-0 flex-1">
                  <h3 className="text-base font-black text-white">{step.title}</h3>
                  <p className="mt-3 text-sm leading-7 text-slate-400">{step.description}</p>
                  {step.danger_flag && (
                    <div className="mt-4 flex gap-2 border border-rose-500/30 bg-rose-950/25 p-3 text-sm leading-6 text-rose-300">
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                      <span>{step.danger_message || "Pay close attention to safety constraints during this stage."}</span>
                    </div>
                  )}
                  {step.affected_components?.length > 0 && (
                    <div className="mt-4 flex flex-wrap gap-2">
                      {step.affected_components.map((part: string) => (
                        <span key={part} className="border border-[#2a2c33] px-2 py-1 text-[10px] font-black uppercase tracking-widest text-slate-500">
                          {part}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </section>
          ))}
        </div>

        <div className="border border-[#2a2c33] bg-[#17181d] p-5">
          <div className="mb-4 flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-cyan-400" />
            <h3 className="text-sm font-black uppercase tracking-widest text-white">Safety Audit</h3>
          </div>
          {issues.length ? (
            <div className="space-y-3">
              {issues.map((issue, index) => (
                <div key={`${issue.description}-${index}`} className="border border-[#2a2c33] bg-[#141519] p-3">
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">{issue.severity} / {issue.category}</div>
                  <p className="mt-2 text-xs leading-6 text-slate-400">{issue.description}</p>
                </div>
              ))}
            </div>
          ) : (
            <div className="border border-emerald-500/30 bg-emerald-950/25 p-4 text-xs leading-6 text-emerald-300">
              All electrical nets validated safely.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function JobsPanel({
  jobs,
  loading,
  error,
  statusFilter,
  onStatusFilterChange,
  onRefresh,
  onOpenProject,
  findProjectForJob,
  lastUpdatedAt,
  pollIntervalMs,
  compact = false,
  title = "Jobs",
  description,
  emptyMessage = "No jobs recorded for this filter.",
}: {
  jobs: A2AJob[];
  loading: boolean;
  error: string | null;
  statusFilter: string;
  onStatusFilterChange: (status: string) => void;
  onRefresh: () => void;
  onOpenProject: (job: A2AJob) => void;
  findProjectForJob: (job: A2AJob) => any;
  lastUpdatedAt: string | null;
  pollIntervalMs: number;
  compact?: boolean;
  title?: string;
  description?: string;
  emptyMessage?: string;
}) {
  const visibleJobs = compact ? jobs.slice(0, 5) : jobs;
  const filters = ["all", "queued", "running", "succeeded", "failed"];
  const panelDescription = description || `A2A job metadata from SQLite. Polling every ${Math.round(pollIntervalMs / 1000)}s.`;

  return (
    <div className={`${compact ? "border border-[#2c2f37] bg-[#17181d] p-5" : "h-full overflow-y-auto bg-[#141519] p-6"}`}>
      <div className="mb-5 flex items-start justify-between gap-4 border-b border-[#2a2c33] pb-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-cyan-400" />
            <h2 className="text-base font-black uppercase text-white">{title}</h2>
          </div>
          <p className="mt-2 text-xs leading-5 text-slate-500">
            {panelDescription}
          </p>
          {lastUpdatedAt && (
            <p className="mt-1 text-[11px] leading-5 text-slate-600">Updated {formatJobTime(lastUpdatedAt)}</p>
          )}
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="flex h-10 w-10 shrink-0 items-center justify-center border border-[#2a2c33] text-slate-400 hover:bg-white hover:text-black"
          title="Refresh jobs"
          aria-label="Refresh jobs"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {filters.map((filter) => (
          <button
            key={filter}
            type="button"
            onClick={() => onStatusFilterChange(filter)}
            className={`border px-3 py-2 text-xs font-bold uppercase ${
              statusFilter === filter
                ? "border-white bg-white text-black"
                : "border-[#2a2c33] bg-[#141519] text-slate-500 hover:border-slate-500 hover:text-white"
            }`}
          >
            {filter}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-4 flex gap-2 border border-amber-500/30 bg-amber-950/25 p-3 text-xs leading-5 text-amber-300">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {loading && !visibleJobs.length ? (
        <div className="border border-[#2a2c33] bg-[#141519] p-5 text-sm text-slate-500">Loading jobs...</div>
      ) : visibleJobs.length ? (
        <div className="space-y-3">
          {visibleJobs.map((job) => (
            <JobRow
              key={job.job_id}
              job={job}
              project={findProjectForJob(job)}
              onOpenProject={() => onOpenProject(job)}
              compact={compact}
            />
          ))}
        </div>
      ) : (
        <div className="border border-[#2a2c33] bg-[#141519] p-5 text-sm leading-6 text-slate-500">
          {emptyMessage}
        </div>
      )}

      {compact && jobs.length > visibleJobs.length && (
        <button
          type="button"
          onClick={() => onStatusFilterChange(statusFilter)}
          className="mt-4 flex w-full items-center justify-center gap-2 border border-[#2a2c33] px-4 py-3 text-xs font-black uppercase text-white hover:bg-white hover:text-black"
        >
          <Database className="h-4 w-4" />
          {jobs.length} total jobs
        </button>
      )}
    </div>
  );
}

function JobRow({
  job,
  project,
  onOpenProject,
  compact,
}: {
  job: A2AJob;
  project: any;
  onOpenProject: () => void;
  compact?: boolean;
}) {
  const tone = statusTone(job.status);
  const summary = job.result_summary || {};
  const title = summary.title || job.payload?.prompt || job.action;
  const prompt = job.payload?.prompt || job.correlation_id || job.job_id;
  const isOpenable = Boolean(project?.project_id);

  return (
    <article className="border border-[#2a2c33] bg-[#141519] p-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className={`inline-flex items-center gap-1.5 border px-2 py-1 text-[11px] font-black uppercase ${tone}`}>
              {job.status === "succeeded" ? <CheckCircle className="h-3.5 w-3.5" /> : job.status === "failed" ? <AlertTriangle className="h-3.5 w-3.5" /> : <RefreshCw className="h-3.5 w-3.5" />}
              {job.status}
            </span>
            <span className="truncate text-[11px] font-bold text-slate-500">{job.sender} {"->"} {job.recipient}</span>
          </div>
          <h3 className="truncate text-sm font-black text-white">{title}</h3>
          <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-500">{prompt}</p>
        </div>

        <button
          type="button"
          onClick={onOpenProject}
          disabled={!isOpenable}
          className="inline-flex h-10 shrink-0 items-center justify-center gap-2 border border-[#2a2c33] px-3 text-xs font-black uppercase text-white hover:bg-white hover:text-black disabled:cursor-not-allowed disabled:opacity-35"
        >
          <Eye className="h-4 w-4" />
          Open
        </button>
      </div>

      <div className={`mt-4 grid gap-2 text-[11px] ${compact ? "grid-cols-2" : "sm:grid-cols-6"}`}>
        <JobMetric label="Job" value={job.job_id} />
        <JobMetric label="Created" value={formatJobTime(job.created_at)} />
        <JobMetric label="Duration" value={formatJobDuration(job)} />
        <JobMetric label="Parts" value={summary.component_count ?? "-"} />
        <JobMetric label="Valid" value={summary.is_valid === undefined ? "-" : summary.is_valid ? "yes" : "no"} />
        <JobMetric label="Image" value={summary.has_product_image ? summary.product_image_model || "yes" : "-"} />
      </div>

      {job.error && (
        <div className="mt-3 border border-rose-500/30 bg-rose-950/20 p-3 text-xs leading-5 text-rose-300">
          {job.error}
        </div>
      )}
    </article>
  );
}

function JobMetric({ label, value }: { label: string; value: any }) {
  return (
    <div className="min-w-0 border border-[#25272e] bg-[#17181d] px-3 py-2">
      <div className="text-[10px] font-black uppercase text-slate-600">{label}</div>
      <div className="mt-1 truncate text-xs font-bold text-slate-300">{String(value ?? "-")}</div>
    </div>
  );
}

function statusTone(status: string) {
  if (status === "succeeded") return "border-emerald-500/30 bg-emerald-950/25 text-emerald-300";
  if (status === "failed") return "border-rose-500/30 bg-rose-950/25 text-rose-300";
  if (status === "running") return "border-cyan-500/30 bg-cyan-950/25 text-cyan-300";
  if (status === "queued") return "border-amber-500/30 bg-amber-950/25 text-amber-300";
  return "border-slate-500/30 bg-slate-900 text-slate-300";
}

function formatJobTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function formatJobDuration(job: A2AJob) {
  if (!job.started_at || !job.completed_at) return job.status === "running" ? "running" : "-";
  const start = new Date(job.started_at).getTime();
  const end = new Date(job.completed_at).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return "-";
  const seconds = Math.max(1, Math.round((end - start) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function PartsSidebar({ components, issues, isValid }: { components: any[]; issues: any[]; isValid: boolean }) {
  return (
    <aside className="hidden min-h-0 border-l border-[#282a30] bg-[#17181d] xl:flex xl:flex-col">
      <div className="border-b border-[#282a30] p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Box className="h-4 w-4 text-slate-500" />
            <h2 className="text-sm font-black uppercase tracking-[0.2em] text-slate-400">Parts List</h2>
          </div>
          <span className="border border-[#30323a] px-2 py-1 text-[10px] text-slate-500">{components.length}</span>
        </div>
      </div>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto px-4 py-4">
        {components.map((component, index) => {
          const tone = categoryTone[component.category?.toLowerCase()] || categoryTone.default;
          const Icon = iconForCategory(component.category);
          return (
            <div key={`${component.ref_des}-${index}`} className="flex min-w-0 items-center gap-3 py-1.5">
              <Icon className={`h-4 w-4 shrink-0 ${tone.text}`} />
              <span className="truncate text-sm font-bold text-slate-300">{component.name}</span>
            </div>
          );
        })}
      </div>
      <div className="border-t border-[#282a30] p-4">
        <div className={`flex items-center gap-2 border p-3 text-xs font-black uppercase tracking-widest ${
          isValid ? "border-emerald-500/30 bg-emerald-950/20 text-emerald-300" : "border-rose-500/30 bg-rose-950/20 text-rose-300"
        }`}>
          {isValid ? <CheckCircle className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
          {isValid ? "Circuit Approved" : `${issues.length} Issues`}
        </div>
      </div>
    </aside>
  );
}

function ProductRender({ product }: { product?: string }) {
  return (
    <div className="relative flex h-[440px] items-center justify-center overflow-hidden bg-[#d5d5d3]">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_45%_38%,rgba(255,255,255,0.88),rgba(210,210,208,0.35)_48%,rgba(185,185,182,0.55))]" />
      <div className="relative h-64 w-[470px] rotate-[-16deg] skew-x-[-8deg] rounded-[34px] border border-black/20 bg-gradient-to-br from-[#6b6b68] via-[#3f403d] to-[#222321] shadow-2xl">
        <div className="absolute left-9 top-8 h-48 w-[400px] rounded-[28px] border border-white/10 bg-gradient-to-br from-[#888884] via-[#4f504d] to-[#262725]" />
        <div className="absolute right-14 top-10 h-28 w-44 rounded-xl border border-black/40 bg-[#0c0d10] shadow-inner">
          <div className="absolute left-5 top-10 h-px w-32 bg-cyan-300/70 shadow-[12px_-10px_0_rgba(103,232,249,0.45),30px_12px_0_rgba(103,232,249,0.6),58px_-2px_0_rgba(103,232,249,0.5)]" />
          <div className="absolute bottom-4 left-6 flex gap-5 text-white/60">
            <span className="h-3 w-3 border-l-4 border-y-4 border-y-transparent" />
            <span className="h-3 w-3 border-l-4 border-y-4 border-y-transparent" />
            <span className="h-3 w-3 bg-white/60" />
          </div>
        </div>
        <div className="absolute left-28 top-28 h-28 w-28 rounded-full border-[10px] border-[#222] bg-[#565653] shadow-inner">
          <div className="absolute left-1/2 top-1/2 h-16 w-16 -translate-x-1/2 -translate-y-1/2 rounded-full border border-black/40 bg-[#8a8a84]" />
          <div className="absolute left-[42px] top-[38px] h-0 w-0 border-y-[12px] border-l-[18px] border-y-transparent border-l-[#4a4a48]" />
        </div>
        <span className="absolute left-20 top-24 h-9 w-9 rounded-full border border-black/40 bg-[#777771]" />
        <span className="absolute left-[104px] top-42 h-8 w-8 rounded-full border border-black/40 bg-[#777771]" />
        <span className="absolute right-5 top-32 h-12 w-3 rounded bg-black/50" />
      </div>
      <div className="absolute bottom-6 right-8 text-[10px] font-black uppercase tracking-[0.3em] text-slate-500">
        {product === "pocket_mp3_player" ? "Rendered from extracted MP3 player features" : "Generated visual reference"}
      </div>
    </div>
  );
}

function SummaryRow({ label, parts, cost, strong = false }: { label: string; parts: number; cost: number; strong?: boolean }) {
  return (
    <div className={`grid grid-cols-3 border-b border-[#2a2c33] px-4 py-3 text-base last:border-b-0 ${strong ? "font-black text-white" : "text-slate-300"}`}>
      <span>{label}</span>
      <span className="text-center">{parts}</span>
      <span className="text-right">${cost.toFixed(2)}</span>
    </div>
  );
}

function CategoryBadge({ category }: { category: string }) {
  const tone = categoryTone[category?.toLowerCase()] || categoryTone.default;
  const Icon = iconForCategory(category);
  return (
    <span className={`mt-4 inline-flex items-center gap-1.5 border ${tone.border} ${tone.bg} px-3 py-2 text-[10px] font-black uppercase tracking-widest ${tone.text}`}>
      <Icon className="h-3 w-3" />
      {tone.label}
    </span>
  );
}

function PartThumb({ component }: { component: any }) {
  const tone = categoryTone[component.category?.toLowerCase()] || categoryTone.default;
  const Icon = iconForCategory(component.category);
  return (
    <div className="flex h-[104px] w-[104px] shrink-0 items-center justify-center bg-white">
      <div className={`flex h-16 w-16 items-center justify-center border ${tone.border} ${tone.bg}`}>
        <Icon className={`h-9 w-9 ${tone.text}`} />
      </div>
    </div>
  );
}

function getSourcesForComponent(component: any) {
  const category = component.category?.toLowerCase();
  if (category === "actuator") {
    return [
      { label: "AliExpress", className: "bg-orange-600" },
      { label: "amazon", className: "bg-amber-400" },
      { label: "eBay", className: "bg-blue-600 text-white" },
    ];
  }
  if (category === "power" && component.name?.toLowerCase().includes("charger")) {
    return [
      { label: "amazon", className: "bg-amber-400" },
      { label: "eBay", className: "bg-blue-600 text-white" },
    ];
  }
  return [{ label: component.category?.toLowerCase() === "mechanical" || component.category?.toLowerCase() === "3d print" ? "fabricate" : "eBay", className: "bg-blue-600 text-white" }];
}

function iconForCategory(category = "") {
  const cat = category.toLowerCase();
  if (cat === "microcontroller") return Cpu;
  if (cat === "sensor") return Database;
  if (cat === "power") return Battery;
  if (cat === "display") return Monitor;
  if (cat === "actuator") return Volume2;
  if (cat === "passives") return Sliders;
  if (cat === "mechanical") return Wrench;
  if (cat === "3d print") return Printer;
  return Box;
}

function MechanicalLabel({ label, index }: { label: string; index: number }) {
  const positions = [
    "left-[36%] top-[19%]",
    "left-[56%] top-[27%]",
    "left-[51%] top-[31%]",
    "left-[42%] top-[48%]",
    "left-[34%] top-[52%]",
    "left-[46%] top-[58%]",
    "left-[48%] top-[63%]",
    "left-[45%] top-[74%]",
    "left-[27%] top-[82%]",
    "left-[24%] top-[86%]",
  ];
  const sizes = index === 4 ? "text-lg" : index > 7 ? "text-sm" : "text-xs";
  return (
    <div className={`absolute ${positions[index]} ${sizes} bg-black/88 px-3 py-1 font-black uppercase tracking-[0.12em] text-violet-300 shadow-lg`}>
      <span className="absolute -left-1 top-0 h-full w-px bg-violet-300" />
      <span>{label}</span>
      <span className="absolute left-1/2 top-full h-40 w-px bg-violet-200/25" />
    </div>
  );
}

function layerColor(key: string) {
  if (key === "structural") return "text-cyan-400";
  if (key === "enclosure") return "text-emerald-400";
  if (key === "mechanism") return "text-amber-400";
  if (key === "print") return "text-violet-300";
  return "text-slate-400";
}

function emptyMetrics() {
  return { electricalParts: 0, mechanicalParts: 0, totalParts: 0, electricalCost: 0, mechanicalCost: 0, totalCost: 0 };
}
