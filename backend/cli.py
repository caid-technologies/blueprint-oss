from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_API_URL = "http://127.0.0.1:8000"


def _api_url(value: str) -> str:
    return value.rstrip("/")


def _fetch_json(url: str) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        return exc.code, payload


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _format_cell(value: Any, width: int) -> str:
    text = "" if value is None else str(value)
    if len(text) > width:
        text = text[: max(0, width - 3)] + "..."
    return text.ljust(width)


def _print_jobs_table(jobs: list[dict[str, Any]]) -> None:
    if not jobs:
        print("No jobs found.")
        return

    columns = [
        ("status", 10),
        ("sender", 12),
        ("action", 28),
        ("job_id", 34),
        ("updated_at", 24),
        ("error", 32),
    ]
    header = "  ".join(_format_cell(name, width) for name, width in columns)
    print(header)
    print("  ".join("-" * width for _, width in columns))
    for job in jobs:
        print("  ".join(_format_cell(job.get(name), width) for name, width in columns))


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    api_url = _api_url(args.api_url)
    checks = {
        "root": f"{api_url}/",
        "jobs": f"{api_url}/api/a2a/jobs?limit=1",
        "components": f"{api_url}/api/components",
    }
    results: dict[str, Any] = {"api_url": api_url, "checks": {}}
    ok = True

    for name, url in checks.items():
        try:
            status, payload = _fetch_json(url)
            check_ok = 200 <= status < 300
            ok = ok and check_ok
            results["checks"][name] = {
                "ok": check_ok,
                "status": status,
                "summary": _summarize_payload(payload),
            }
        except urllib.error.URLError as exc:
            ok = False
            results["checks"][name] = {
                "ok": False,
                "error": str(exc.reason),
            }

    _print_json(results)
    return 0 if ok else 1


def cmd_jobs(args: argparse.Namespace) -> int:
    if args.local:
        from backend.job_store import JobMetadataStore

        jobs = JobMetadataStore(args.db_path).list_jobs(
            sender=args.sender,
            status=args.status,
            limit=args.limit,
        )
    else:
        api_url = _api_url(args.api_url)
        params = {"limit": str(args.limit)}
        if args.sender:
            params["sender"] = args.sender
        if args.status:
            params["status"] = args.status
        url = f"{api_url}/api/a2a/jobs?{urllib.parse.urlencode(params)}"
        try:
            status, payload = _fetch_json(url)
        except urllib.error.URLError as exc:
            print(f"Could not reach backend at {api_url}: {exc.reason}", file=sys.stderr)
            return 1
        if status < 200 or status >= 300:
            print(f"Jobs endpoint returned HTTP {status}: {payload}", file=sys.stderr)
            return 1
        jobs = payload if isinstance(payload, list) else []

    if args.json:
        _print_json(jobs)
    else:
        _print_jobs_table(jobs)
    return 0


def cmd_seed(_: argparse.Namespace) -> int:
    from backend.seed_db import seed_database

    seed_database()
    return 0


def _summarize_payload(payload: Any) -> Any:
    if isinstance(payload, list):
        return {"items": len(payload)}
    if isinstance(payload, dict):
        if "status" in payload or "service" in payload:
            return {key: payload.get(key) for key in ("status", "service", "version") if key in payload}
        return {"keys": sorted(payload.keys())[:12]}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blueprint-backend",
        description="Backend utility CLI for the Blueprint FastAPI service.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the FastAPI backend with uvicorn.")
    serve.add_argument("--host", default="127.0.0.1", help="Host to bind. Defaults to 127.0.0.1.")
    serve.add_argument("--port", default=8000, type=int, help="Port to bind. Defaults to 8000.")
    serve.add_argument("--reload", action="store_true", help="Enable uvicorn reload.")
    serve.set_defaults(func=cmd_serve)

    health = subparsers.add_parser("health", help="Check backend root, jobs, and component endpoints.")
    health.add_argument("--api-url", default=DEFAULT_API_URL, help=f"Backend URL. Defaults to {DEFAULT_API_URL}.")
    health.set_defaults(func=cmd_health)

    jobs = subparsers.add_parser("jobs", help="List A2A job metadata.")
    jobs.add_argument("--api-url", default=DEFAULT_API_URL, help=f"Backend URL. Defaults to {DEFAULT_API_URL}.")
    jobs.add_argument("--status", choices=["queued", "running", "routed", "succeeded", "failed"], help="Filter by job status.")
    jobs.add_argument("--sender", help="Filter by sender.")
    jobs.add_argument("--limit", default=50, type=int, help="Maximum jobs to show, from 1 to 200.")
    jobs.add_argument("--json", action="store_true", help="Print raw JSON.")
    jobs.add_argument("--local", action="store_true", help="Read directly from the local SQLite job store.")
    jobs.add_argument("--db-path", help="SQLite job database path for --local. Defaults to JOB_METADATA_DB_PATH.")
    jobs.set_defaults(func=cmd_jobs)

    seed = subparsers.add_parser("seed", help="Initialize and seed the component database.")
    seed.set_defaults(func=cmd_seed)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
