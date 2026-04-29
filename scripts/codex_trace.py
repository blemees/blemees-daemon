#!/usr/bin/env python3
"""Capture a JSON-RPC trace from `codex mcp-server` for spec work.

Drives the server via stdio with a small scripted sequence:

    initialize -> notifications/initialized -> tools/list -> [tools/call]

and writes every line on stdin / stdout / stderr to `docs/traces/`. Used
once, by hand, to ground the agent.* event mapping in real wire shapes.
Not part of the daemon — invoke it directly:

    python scripts/codex_trace.py --phase list   # stop after tools/list
    python scripts/codex_trace.py --phase turn   # also do a 1-turn tools/call

Passing `--phase turn` runs a real model call (consumes credits).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACES = ROOT / "docs" / "traces"


def jsonrpc_request(req_id: int | str, method: str, params: dict | None = None) -> dict:
    msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def jsonrpc_notify(method: str, params: dict | None = None) -> dict:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


async def drain(reader: asyncio.StreamReader, sink: list[dict | str]) -> None:
    while True:
        raw = await reader.readline()
        if not raw:
            return
        line = raw.rstrip(b"\r\n").decode("utf-8", errors="replace")
        if not line:
            continue
        try:
            sink.append(json.loads(line))
        except json.JSONDecodeError:
            sink.append({"_non_json": line})


async def drain_stderr(reader: asyncio.StreamReader, sink: list[str]) -> None:
    while True:
        raw = await reader.readline()
        if not raw:
            return
        sink.append(raw.rstrip(b"\r\n").decode("utf-8", errors="replace"))


async def run(phase: str) -> int:
    proc = await asyncio.create_subprocess_exec(
        "codex",
        "mcp-server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin and proc.stdout and proc.stderr

    stdout: list[dict | str] = []
    stderr: list[str] = []
    sent: list[dict] = []

    stdout_task = asyncio.create_task(drain(proc.stdout, stdout))
    stderr_task = asyncio.create_task(drain_stderr(proc.stderr, stderr))

    async def send(msg: dict) -> None:
        sent.append(msg)
        proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    await send(
        jsonrpc_request(
            1,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "blemees-trace", "version": "0"},
            },
        )
    )
    # Give the server a beat to reply before we send the initialized notice.
    await asyncio.sleep(0.5)
    await send(jsonrpc_notify("notifications/initialized"))
    await send(jsonrpc_request(2, "tools/list"))
    await asyncio.sleep(1.0)

    if phase == "turn":
        await send(
            jsonrpc_request(
                3,
                "tools/call",
                {
                    "name": "codex",
                    "arguments": {
                        "prompt": "Reply with exactly: pong.",
                        "approval-policy": "never",
                        "sandbox": "read-only",
                    },
                },
            )
        )
        # Allow time for the turn to complete (text round-trip).
        await asyncio.sleep(20.0)

    proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except TimeoutError:
        proc.kill()
        await proc.wait()

    await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

    TRACES.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%S")
    out = TRACES / f"codex-mcp-{phase}-{ts}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        fh.write(f"# codex mcp-server trace, phase={phase}, ts={ts}\n")
        fh.write("# codex --version: see meta\n")
        for msg in sent:
            fh.write("> " + json.dumps(msg) + "\n")
        for msg in stdout:
            fh.write("< " + json.dumps(msg) + "\n")
        if stderr:
            fh.write("# stderr:\n")
            for line in stderr:
                fh.write("# " + line + "\n")
    print(f"wrote {out} ({len(stdout)} stdout msgs, {len(stderr)} stderr lines)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", choices=("list", "turn"), default="list")
    args = ap.parse_args()
    return asyncio.run(run(args.phase))


if __name__ == "__main__":
    sys.exit(main())
