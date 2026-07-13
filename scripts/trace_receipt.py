"""Task 12 tooling: trace a receipt's Cloud Run logs by message_id/webhook_event_id.

Wraps `gcloud logging read` so "trace by message_id" (the E2E runbook's own stated
verification method — see checklist3.md Task 12) is one copy-pasteable command instead
of a hand-written Cloud Logging filter each time. Reads structured fields
(`app/logging_setup.py`'s JsonFormatter output, e.g. message_id/webhook_event_id) via
exact match, and falls back to a substring match on the log message itself for lines
that only %-interpolate the id (e.g. app/tasks.py's "duplicate delivery collapsed"
line, or app/gcs.py's "deleted blob <name>" line).

Prerequisites: `gcloud` CLI installed and authenticated (`gcloud auth login`), GCP_PROJECT
set in .env or passed via --project.

Usage:
  uv run python scripts/trace_receipt.py msg-abc123
  uv run python scripts/trace_receipt.py 01WHIMAGE0000000000000001 --minutes 15
  uv run python scripts/trace_receipt.py Callowedgroupidxxxxxxxxxxxxxxxxxxx --minutes 5
  uv run python scripts/trace_receipt.py --minutes 30           # no id: everything recent
  uv run python scripts/trace_receipt.py msg-abc123 --raw       # untouched JSON entries
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()

_SERVICES = '("webhook-service" OR "worker-service")'
_NON_EXTRA_KEYS = {"message", "logger", "severity", "exc_info"}


def _build_filter(trace_id: str | None) -> str:
    base = f'resource.type="cloud_run_revision" AND resource.labels.service_name={_SERVICES}'
    if not trace_id:
        return base
    escaped = trace_id.replace("\\", "\\\\").replace('"', '\\"')
    id_clause = (
        f'(jsonPayload.message_id="{escaped}" OR jsonPayload.webhook_event_id="{escaped}" '
        f'OR jsonPayload.message:"{escaped}")'
    )
    return f"{base} AND {id_clause}"


def _render(entry: dict) -> None:
    ts = entry.get("timestamp", "?")
    service = entry.get("resource", {}).get("labels", {}).get("service_name", "?")
    severity = entry.get("severity", "DEFAULT")

    payload = entry.get("jsonPayload")
    if payload is not None:
        message = payload.get("message", "")
        extras = {k: v for k, v in payload.items() if k not in _NON_EXTRA_KEYS}
        extras_str = " ".join(f"{k}={v}" for k, v in extras.items())
        line = f"{ts}  {service:14s}  {severity:7s}  {message}"
        if extras_str:
            line += f"  [{extras_str}]"
        print(line)
        exc_info = payload.get("exc_info")
        if exc_info:
            first_line = str(exc_info).splitlines()[0]
            print(f"    {first_line}")
        return

    http_request = entry.get("httpRequest")
    if http_request:
        method = http_request.get("requestMethod", "?")
        status = http_request.get("status", "?")
        url = http_request.get("requestUrl", "?")
        print(f"{ts}  {service:14s}  {severity:7s}  {method} {status} {url}")
        return

    text = entry.get("textPayload", "")
    print(f"{ts}  {service:14s}  {severity:7s}  {text}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "trace_id",
        nargs="?",
        default=None,
        help="message_id, webhook_event_id, or any substring (e.g. a group id). Omit for everything.",
    )
    parser.add_argument("--minutes", type=int, default=60, help="how far back to search (default: 60)")
    parser.add_argument("--limit", type=int, default=200, help="max log entries (default: 200)")
    parser.add_argument("--project", default=None, help="GCP project (default: $GCP_PROJECT)")
    parser.add_argument("--raw", action="store_true", help="print untouched JSON entries instead")
    args = parser.parse_args()

    gcloud = shutil.which("gcloud")
    if gcloud is None:
        raise SystemExit("gcloud CLI not found on PATH — install/auth the Google Cloud SDK first.")

    project = args.project or os.environ.get("GCP_PROJECT")
    if not project:
        raise SystemExit("No project: pass --project or set GCP_PROJECT in .env.")

    log_filter = _build_filter(args.trace_id)
    cmd = [
        gcloud,
        "logging",
        "read",
        log_filter,
        f"--project={project}",
        f"--freshness={args.minutes}m",
        f"--limit={args.limit}",
        "--format=json",
        "--order=asc",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    entries = json.loads(result.stdout or "[]")
    if not entries:
        print(f"No log entries in the last {args.minutes}m matching: {log_filter}")
        return

    if args.raw:
        print(json.dumps(entries, indent=2, default=str))
        return

    for entry in entries:
        _render(entry)


if __name__ == "__main__":
    main()
