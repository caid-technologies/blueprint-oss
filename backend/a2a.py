import asyncio
import base64
import contextlib
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from backend.agents.orchestrator import HardwarePipelineOrchestrator
from backend.database import DBGeneratedProject, SessionLocal
from backend.image_providers import build_image_provider, get_image_output_debug_config
from backend.job_store import JOB_STORE
from backend.models import ComponentInstance, ConnectionNet
from backend.utils import generate_mermaid_chart, generate_svg_schematic
from backend.validation import validate_circuit


logger = logging.getLogger(__name__)

BLUEPRINT_AGENT_ID = "blueprint"
SERVER_RECIPIENTS = {BLUEPRINT_AGENT_ID, "server", "hardware_pipeline", "hardware-compiler"}


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _payload_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


class A2AAgentRegistration(BaseModel):
    agent_id: Optional[str] = Field(None, description="Stable agent identifier")
    name: Optional[str] = Field(None, description="Human-readable agent name")
    capabilities: List[str] = Field(default_factory=list, description="Capability labels this agent provides")
    transports: List[str] = Field(default_factory=list, description="Transports the agent can use")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional agent metadata")


class A2AMessage(BaseModel):
    job_id: str = Field(default_factory=lambda: f"job_{uuid.uuid4().hex}")
    message_id: str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex}")
    type: str = Field("task", description="Message type such as task, event, result, error, or ping")
    action: str = Field("blueprint.generate_project", description="Action name or tool name")
    sender: str = Field("anonymous", description="Sending agent id")
    recipient: str = Field(BLUEPRINT_AGENT_ID, description="Recipient agent id")
    correlation_id: Optional[str] = Field(None, description="Optional id used to correlate request/result pairs")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Message-specific JSON payload")


class A2AEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex}")
    job_id: Optional[str] = None
    message_id: Optional[str] = None
    correlation_id: Optional[str] = None
    type: str = "event"
    action: str
    sender: str = BLUEPRINT_AGENT_ID
    recipient: str
    created_at: str = Field(default_factory=_utc_now)
    payload: Dict[str, Any] = Field(default_factory=dict)


class A2AHub:
    """In-memory event broker for lightweight agent-to-agent handoffs."""

    def __init__(self) -> None:
        self._queues: Dict[str, asyncio.Queue[A2AEvent]] = {}
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._history: Dict[str, List[A2AEvent]] = {}
        self._lock = asyncio.Lock()

    async def register(self, agent_id: str, registration: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        async with self._lock:
            if agent_id not in self._queues:
                self._queues[agent_id] = asyncio.Queue()
            current = self._agents.get(agent_id, {})
            self._agents[agent_id] = {
                **current,
                **(registration or {}),
                "agent_id": agent_id,
                "last_seen_at": _utc_now(),
            }
            self._history.setdefault(agent_id, [])
            return self._agents[agent_id]

    async def publish(self, event: A2AEvent) -> A2AEvent:
        await self.register(event.recipient)
        queue = self._queues[event.recipient]
        await queue.put(event)
        history = self._history.setdefault(event.recipient, [])
        history.append(event)
        del history[:-100]
        return event

    async def poll(self, agent_id: str, timeout: float = 25.0, limit: int = 10) -> List[A2AEvent]:
        await self.register(agent_id)
        queue = self._queues[agent_id]
        events: List[A2AEvent] = []

        if limit <= 0:
            return events

        if queue.empty() and timeout > 0:
            try:
                events.append(await asyncio.wait_for(queue.get(), timeout=timeout))
            except asyncio.TimeoutError:
                return events

        while len(events) < limit:
            try:
                events.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        return events

    def snapshot(self) -> Dict[str, Any]:
        return {
            "agents": list(self._agents.values()),
            "queued_events": {agent_id: queue.qsize() for agent_id, queue in self._queues.items()},
        }


A2A_HUB = A2AHub()


def get_a2a_capabilities() -> Dict[str, Any]:
    return {
        "agent_id": BLUEPRINT_AGENT_ID,
        "name": "Blueprint OSS Hardware Compiler",
        "transports": {
            "rest": {
                "capabilities": "/api/a2a/capabilities",
                "register": "/api/a2a/agents/{agent_id}",
                "send_message": "/api/a2a/messages",
                "listen": "/api/a2a/agents/{agent_id}/events",
            },
            "websocket": {"listen": "/api/a2a/socket/{agent_id}"},
            "tcp_jsonl": {
                "enabled_env": "A2A_SOCKET_ENABLED=true",
                "host_env": "A2A_SOCKET_HOST",
                "port_env": "A2A_SOCKET_PORT",
            },
            "mcp": {
                "endpoint": "/mcp",
                "alias": "/api/a2a/mcp",
                "tools": [
                    "blueprint.generate_project",
                    "blueprint.debug_config",
                    "blueprint.validate_circuit",
                    "blueprint.a2a.send_message",
                    "blueprint.a2a.poll_events",
                    "blueprint.a2a.get_job",
                    "blueprint.a2a.list_jobs",
                ],
            },
        },
        "job_metadata": {
            "store": "sqlite",
            "path_env": "JOB_METADATA_DB_PATH",
            "default_path": "./blueprint_jobs.db",
        },
        "image_output": get_image_output_debug_config(),
        "actions": [
            "blueprint.generate_project",
            "blueprint.debug_config",
            "blueprint.validate_circuit",
            "blueprint.a2a.capabilities",
            "blueprint.a2a.get_job",
            "blueprint.a2a.list_jobs",
            "a2a.ping",
        ],
        "hub": A2A_HUB.snapshot(),
    }


def _decode_image_data(image_data: Optional[str]) -> Tuple[Optional[bytes], Optional[str]]:
    if not image_data:
        return None, None

    base64_data = image_data.strip()
    image_mime_type = None
    if "," in image_data:
        header, base64_data = image_data.split(",", 1)
        if "data:" in header and ";base64" in header:
            image_mime_type = header.split(";")[0].replace("data:", "")
        base64_data = base64_data.strip()

    return base64.b64decode(base64_data), image_mime_type or "image/png"


def _attach_product_image(prompt_text: str, ir: Any, generate_image: bool = False) -> None:
    image_provider = build_image_provider(force_enabled=generate_image)
    image_config = image_provider.get_debug_config()
    metadata = {
        **(ir.assembly_metadata or {}),
        "image_output_requested": generate_image,
        "image_output_enabled": image_config.get("enabled", False),
        "image_output_provider": image_config.get("provider"),
        "image_output_model": image_config.get("model_name"),
        "image_output_configured": image_config.get("configured", False),
    }
    ir.assembly_metadata = metadata

    if not generate_image:
        return

    try:
        generated_image = image_provider.generate_project_image(prompt_text, ir)
    except Exception as exc:
        logger.warning("Product image generation failed: %s", exc)
        ir.assembly_metadata = {
            **(ir.assembly_metadata or {}),
            "product_image_error": str(exc)[:500],
        }
        return

    if not generated_image:
        return

    ir.assembly_metadata = {
        **(ir.assembly_metadata or {}),
        "product_image_data": generated_image.data_url,
        "product_image_provider": generated_image.provider,
        "product_image_model": generated_image.model,
        "product_image_size": generated_image.size,
        "product_image_output_format": generated_image.output_format,
        "product_image_prompt": generated_image.prompt,
    }


def _persist_updated_project_ir(ir: Any) -> None:
    metadata = ir.assembly_metadata or {}
    project_id = metadata.get("project_id")
    if not project_id:
        return

    db = SessionLocal()
    try:
        project = db.query(DBGeneratedProject).filter(DBGeneratedProject.project_id == project_id).first()
        if not project:
            return
        project.hardware_ir = ir.model_dump()
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Failed to persist updated project metadata for %s: %s", project_id, exc)
    finally:
        db.close()


def build_generation_response(
    prompt: str,
    image_data: Optional[str] = None,
    generate_image: bool = False,
) -> Dict[str, Any]:
    prompt_text = (prompt or "").strip()
    has_prompt = bool(prompt_text)
    if not has_prompt and not image_data:
        raise ValueError("Provide a prompt or reference image.")
    if not has_prompt:
        prompt_text = "Infer a buildable hardware project from the uploaded reference image."

    try:
        image_bytes, image_mime_type = _decode_image_data(image_data)
    except Exception as exc:
        if not has_prompt:
            raise ValueError("Reference image could not be decoded.") from exc
        image_bytes, image_mime_type = None, None

    orchestrator = HardwarePipelineOrchestrator()
    ir = orchestrator.generate_project(prompt_text, image_bytes=image_bytes, image_mime_type=image_mime_type)

    if image_data:
        metadata = ir.assembly_metadata or {}
        ir.assembly_metadata = {
            **metadata,
            "reference_image_data": image_data,
            "image_features": metadata.get("image_features") or ir.constraints[:12],
            "input_mode": "prompt_image",
        }

    _attach_product_image(prompt_text, ir, generate_image=generate_image)
    _persist_updated_project_ir(ir)

    return {
        "project_ir": ir.model_dump(),
        "mermaid_code": generate_mermaid_chart(ir),
        "svg_schematic": generate_svg_schematic(ir),
    }


async def call_blueprint_action(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = action.removeprefix("blueprint.")

    if normalized == "generate_project":
        return await asyncio.to_thread(
            build_generation_response,
            payload.get("prompt", ""),
            payload.get("image_data"),
            _payload_bool(payload.get("generate_image"), default=False),
        )

    if normalized == "debug_config":
        orchestrator = HardwarePipelineOrchestrator()
        return {
            **orchestrator.get_debug_config(),
            "image_output": get_image_output_debug_config(),
        }

    if normalized == "validate_circuit":
        components = [ComponentInstance.model_validate(component) for component in payload.get("components", [])]
        nets = [ConnectionNet.model_validate(net) for net in payload.get("nets", [])]
        issues = validate_circuit(components, nets)
        return {
            "is_valid": not any(issue.severity.upper() == "CRITICAL" for issue in issues),
            "issues": [issue.model_dump() for issue in issues],
        }

    if normalized in {"a2a.capabilities", "capabilities"}:
        return get_a2a_capabilities()

    if action == "a2a.ping" or normalized == "ping":
        return {"pong": True, "server_time": _utc_now()}

    raise ValueError(f"Unsupported Blueprint A2A action: {action}")


def _is_server_message(message: A2AMessage) -> bool:
    return message.recipient in SERVER_RECIPIENTS or message.action.startswith("blueprint.")


async def submit_a2a_message(message: A2AMessage) -> A2AEvent:
    await A2A_HUB.register(message.sender)
    server_owned = _is_server_message(message)
    job = JOB_STORE.create_job(
        job_id=message.job_id,
        message_id=message.message_id,
        correlation_id=message.correlation_id,
        action=message.action,
        sender=message.sender,
        recipient=message.recipient,
        payload=message.payload,
        server_owned=server_owned,
        status="queued" if server_owned else "accepted",
    )

    ack = A2AEvent(
        job_id=message.job_id,
        message_id=message.message_id,
        correlation_id=message.correlation_id,
        type="ack",
        action=message.action,
        sender=BLUEPRINT_AGENT_ID,
        recipient=message.sender,
        payload={"accepted": True, "server_owned": server_owned, "job_id": message.job_id, "job": job},
    )
    await A2A_HUB.publish(ack)

    if server_owned:
        asyncio.create_task(_process_server_message(message))
    else:
        JOB_STORE.mark_routed(message.job_id)
        await A2A_HUB.publish(
            A2AEvent(
                job_id=message.job_id,
                message_id=message.message_id,
                correlation_id=message.correlation_id,
                type=message.type,
                action=message.action,
                sender=message.sender,
                recipient=message.recipient,
                payload=message.payload,
            )
        )

    return ack


async def _process_server_message(message: A2AMessage) -> None:
    JOB_STORE.mark_running(message.job_id)
    try:
        result = await call_blueprint_action(message.action, message.payload)
        JOB_STORE.mark_succeeded(message.job_id, result)
        event = A2AEvent(
            job_id=message.job_id,
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            type="result",
            action=message.action,
            sender=BLUEPRINT_AGENT_ID,
            recipient=message.sender,
            payload=result,
        )
    except Exception as exc:
        JOB_STORE.mark_failed(message.job_id, str(exc))
        event = A2AEvent(
            job_id=message.job_id,
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            type="error",
            action=message.action,
            sender=BLUEPRINT_AGENT_ID,
            recipient=message.sender,
            payload={"error": str(exc)},
        )

    await A2A_HUB.publish(event)


async def handle_a2a_websocket(websocket: WebSocket, agent_id: str) -> None:
    await websocket.accept()
    await A2A_HUB.register(agent_id, {"transports": ["websocket"]})

    sender_task = asyncio.create_task(_websocket_sender(websocket, agent_id))
    try:
        await A2A_HUB.publish(
            A2AEvent(
                type="ready",
                action="a2a.connected",
                sender=BLUEPRINT_AGENT_ID,
                recipient=agent_id,
                payload=get_a2a_capabilities(),
            )
        )
        while True:
            raw_message = await websocket.receive_json()
            if isinstance(raw_message, dict) and raw_message.get("jsonrpc") == "2.0":
                await websocket.send_json(await handle_mcp_json_rpc(raw_message))
                continue

            raw_message = {**raw_message, "sender": raw_message.get("sender") or agent_id}
            await submit_a2a_message(A2AMessage.model_validate(raw_message))
    except WebSocketDisconnect:
        logger.info("A2A websocket disconnected: %s", agent_id)
    finally:
        sender_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender_task


async def _websocket_sender(websocket: WebSocket, agent_id: str) -> None:
    while True:
        events = await A2A_HUB.poll(agent_id, timeout=30.0, limit=10)
        for event in events:
            await websocket.send_json(event.model_dump())


_tcp_server: Optional[asyncio.AbstractServer] = None


async def start_a2a_tcp_server() -> Optional[asyncio.AbstractServer]:
    global _tcp_server
    if _tcp_server is not None or not _env_bool("A2A_SOCKET_ENABLED", default=False):
        return _tcp_server

    host = os.getenv("A2A_SOCKET_HOST", "127.0.0.1")
    port = int(os.getenv("A2A_SOCKET_PORT", "8766"))
    _tcp_server = await asyncio.start_server(_handle_tcp_client, host, port)
    logger.info("A2A TCP JSONL socket listening on %s:%s", host, port)
    return _tcp_server


async def stop_a2a_tcp_server() -> None:
    global _tcp_server
    if _tcp_server is None:
        return
    _tcp_server.close()
    await _tcp_server.wait_closed()
    _tcp_server = None


async def _handle_tcp_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    agent_id = f"tcp_{uuid.uuid4().hex[:12]}"
    await A2A_HUB.register(agent_id, {"transports": ["tcp_jsonl"], "metadata": {"peer": str(peer)}})
    sender_task = asyncio.create_task(_tcp_sender(writer, agent_id))

    await A2A_HUB.publish(
        A2AEvent(
            type="ready",
            action="a2a.connected",
            sender=BLUEPRINT_AGENT_ID,
            recipient=agent_id,
            payload={**get_a2a_capabilities(), "connection_agent_id": agent_id},
        )
    )

    try:
        while not reader.at_eof():
            line = await reader.readline()
            if not line:
                break
            try:
                raw_message = json.loads(line.decode("utf-8"))
                raw_message = {**raw_message, "sender": raw_message.get("sender") or agent_id}
                await submit_a2a_message(A2AMessage.model_validate(raw_message))
            except Exception as exc:
                writer.write(json.dumps({"type": "error", "error": str(exc)}).encode("utf-8") + b"\n")
                await writer.drain()
    finally:
        sender_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender_task
        writer.close()
        await writer.wait_closed()


async def _tcp_sender(writer: asyncio.StreamWriter, agent_id: str) -> None:
    while not writer.is_closing():
        events = await A2A_HUB.poll(agent_id, timeout=30.0, limit=10)
        for event in events:
            writer.write(json.dumps(event.model_dump()).encode("utf-8") + b"\n")
            await writer.drain()


def _jsonrpc_result(request_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str, data: Optional[Any] = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _mcp_tool_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(result)}],
        "structuredContent": result,
    }


def _mcp_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "blueprint.generate_project",
            "description": "Generate a Blueprint Hardware IR package, Mermaid diagram, and SVG schematic.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "image_data": {"type": "string", "description": "Optional data URL or base64 image"},
                    "generate_image": {"type": "boolean", "default": False},
                },
                "required": ["prompt"],
            },
        },
        {
            "name": "blueprint.debug_config",
            "description": "Return configured LLM provider and model resolution details.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "blueprint.validate_circuit",
            "description": "Validate a list of components and nets against Blueprint electrical rules.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "components": {"type": "array", "items": {"type": "object"}},
                    "nets": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["components", "nets"],
            },
        },
        {
            "name": "blueprint.a2a.send_message",
            "description": "Send an A2A message through the Blueprint in-memory broker.",
            "inputSchema": {"type": "object", "properties": A2AMessage.model_json_schema()["properties"]},
        },
        {
            "name": "blueprint.a2a.poll_events",
            "description": "Long-poll queued A2A events for an agent id.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "timeout": {"type": "number", "default": 25},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["agent_id"],
            },
        },
        {
            "name": "blueprint.a2a.get_job",
            "description": "Fetch persisted SQLite metadata for one A2A job.",
            "inputSchema": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
        {
            "name": "blueprint.a2a.list_jobs",
            "description": "List persisted SQLite A2A job metadata.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sender": {"type": "string"},
                    "status": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
            },
        },
    ]


async def handle_mcp_json_rpc(payload: Any) -> Any:
    if isinstance(payload, list):
        return [await _handle_mcp_request(item) for item in payload]
    return await _handle_mcp_request(payload)


async def _handle_mcp_request(request: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(request, dict):
        return _jsonrpc_error(None, -32600, "Invalid JSON-RPC request.")

    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}

    try:
        if method == "initialize":
            requested_version = params.get("protocolVersion") or os.getenv("MCP_PROTOCOL_VERSION", "2024-11-05")
            return _jsonrpc_result(
                request_id,
                {
                    "protocolVersion": requested_version,
                    "serverInfo": {"name": "blueprint-oss", "version": "1.0.0"},
                    "capabilities": {"tools": {}},
                },
            )

        if method in {"notifications/initialized", "ping"}:
            return _jsonrpc_result(request_id, {})

        if method == "tools/list":
            return _jsonrpc_result(request_id, {"tools": _mcp_tools()})

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments") or {}
            result = await _call_mcp_tool(tool_name, arguments)
            return _jsonrpc_result(request_id, _mcp_tool_result(result))

        return _jsonrpc_error(request_id, -32601, f"Unknown MCP method: {method}")
    except Exception as exc:
        return _jsonrpc_error(request_id, -32000, str(exc))


async def _call_mcp_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if tool_name == "blueprint.a2a.send_message":
        ack = await submit_a2a_message(A2AMessage.model_validate(arguments))
        return ack.model_dump()

    if tool_name == "blueprint.a2a.poll_events":
        events = await A2A_HUB.poll(
            arguments["agent_id"],
            timeout=float(arguments.get("timeout", 25)),
            limit=int(arguments.get("limit", 10)),
        )
        return {"events": [event.model_dump() for event in events]}

    if tool_name == "blueprint.a2a.get_job":
        job = JOB_STORE.get_job(arguments["job_id"])
        if not job:
            raise ValueError("A2A job not found.")
        return job

    if tool_name == "blueprint.a2a.list_jobs":
        return {
            "jobs": JOB_STORE.list_jobs(
                sender=arguments.get("sender"),
                status=arguments.get("status"),
                limit=int(arguments.get("limit", 50)),
            )
        }

    return await call_blueprint_action(tool_name, arguments)
