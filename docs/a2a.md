# Agent-To-Agent (A2A)

Blueprint exposes the same hardware generation capability through several agent-friendly transports.

## Transports
- **REST:** `GET /api/a2a/capabilities`, `PUT /api/a2a/agents/{agent_id}`, `POST /api/a2a/messages`, long-poll `GET /api/a2a/agents/{agent_id}/events`, and job metadata lookup under `GET /api/a2a/jobs`
- **WebSocket:** `/api/a2a/socket/{agent_id}`
- **TCP JSONL socket:** optional newline-delimited JSON socket enabled with `A2A_SOCKET_ENABLED=true`
- **MCP-style JSON-RPC:** `POST /mcp` or `POST /api/a2a/mcp`

Job metadata is persisted to SQLite at `JOB_METADATA_DB_PATH` (default: `./blueprint_jobs.db`). The database stores compact metadata only: payloads have image data redacted, and results are summarized instead of storing full generated IR blobs.

## Message Shape
```json
{
  "type": "task",
  "job_id": "job-build-001",
  "action": "blueprint.generate_project",
  "sender": "agent_alpha",
  "recipient": "blueprint",
  "correlation_id": "build-001",
  "payload": {
    "prompt": "ESP32 soil moisture monitor with OLED",
    "generate_image": false
  }
}
```

Server-owned actions queue an `ack` event followed by a `result` or `error` event for the sender. Messages addressed to another agent are brokered into that agent's queue. Every submitted message is persisted with a `job_id` and lifecycle status.

## REST Listen Flow
1. Register an agent:
```bash
curl -X PUT http://localhost:8000/api/a2a/agents/agent_alpha \
  -H 'Content-Type: application/json' \
  -d '{"name":"Agent Alpha","capabilities":["hardware_planning"],"transports":["rest"]}'
```

2. Send a task:
```bash
curl -X POST http://localhost:8000/api/a2a/messages \
  -H 'Content-Type: application/json' \
  -d '{"sender":"agent_alpha","recipient":"blueprint","action":"blueprint.generate_project","payload":{"prompt":"ESP32 soil moisture monitor with OLED","generate_image":false}}'
```

Set `payload.generate_image` to `true` only for jobs that should call the configured image model.

3. Long-poll for queued events:
```bash
curl 'http://localhost:8000/api/a2a/agents/agent_alpha/events?timeout=30&limit=10'
```

4. Fetch persisted job metadata:
```bash
curl http://localhost:8000/api/a2a/jobs/job-build-001
curl 'http://localhost:8000/api/a2a/jobs?sender=agent_alpha&status=succeeded'
```

## WebSocket
Connect to `/api/a2a/socket/{agent_id}` and send the same JSON message shape. The socket receives queued A2A events as JSON objects. It also accepts MCP JSON-RPC envelopes.

## TCP JSONL
Set:
```env
A2A_SOCKET_ENABLED=true
A2A_SOCKET_HOST=127.0.0.1
A2A_SOCKET_PORT=8766
```

Each line sent to the socket is an `A2AMessage` JSON object. Each line returned by the socket is an `A2AEvent` JSON object.

## MCP Tools
`POST /mcp` supports:
- `initialize`
- `tools/list`
- `tools/call`

Available tools:
- `blueprint.generate_project`
- `blueprint.debug_config`
- `blueprint.validate_circuit`
- `blueprint.a2a.send_message`
- `blueprint.a2a.poll_events`
- `blueprint.a2a.get_job`
- `blueprint.a2a.list_jobs`
