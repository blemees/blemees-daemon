"""End-to-end tests that require the real ``codex`` CLI and a live
``codex login`` session. Gated with ``requires_codex`` and auto-skipped
otherwise.

Run with::

    pytest -m requires_codex
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import uuid

import pytest
import pytest_asyncio

from blemees import PROTOCOL_VERSION
from blemees.config import Config
from blemees.daemon import Daemon
from blemees.logging import configure

CODEX = shutil.which("codex")


pytestmark = pytest.mark.requires_codex


def _need_codex() -> None:
    if CODEX is None:
        pytest.skip("`codex` not on PATH", allow_module_level=True)
    # `codex login status` exits 0 when authenticated. Treat any non-zero
    # exit (or process error) as "not logged in" → skip.
    try:
        proc = subprocess.run(
            [CODEX, "login", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"`codex login status` failed to run: {exc}", allow_module_level=True)
    if proc.returncode != 0:
        pytest.skip(
            f"`codex login status` reports not logged in (rc={proc.returncode})",
            allow_module_level=True,
        )


_need_codex()


@pytest_asyncio.fixture
async def real_daemon(tmp_path):
    from tests.blemees.conftest import short_socket_path

    socket_path = short_socket_path("blemeesd-e2e-codex")
    cfg = Config(socket_path=str(socket_path), codex_bin=CODEX)
    logger = configure("error")
    daemon = Daemon(cfg, logger)
    await daemon.start()
    serve_task = asyncio.create_task(daemon.serve_forever())
    try:
        yield str(socket_path)
    finally:
        daemon.request_shutdown()
        try:
            await asyncio.wait_for(serve_task, timeout=5.0)
        except TimeoutError:
            serve_task.cancel()


async def _client(socket_path: str):
    from tests.blemees.conftest import _StreamClient  # reuse helper

    reader, writer = await asyncio.open_unix_connection(socket_path)
    c = _StreamClient(reader, writer)
    await c.send({"type": "blemeesd.hello", "client": "e2e-codex/0", "protocol": PROTOCOL_VERSION})
    ack = await c.recv()
    assert ack["type"] == "blemeesd.hello_ack"
    return c


def _open_codex(session: str) -> dict:
    return {
        "type": "blemeesd.open",
        "id": "r1",
        "session_id": session,
        "backend": "codex",
        "options": {
            "codex": {
                "sandbox": "read-only",
                "approval-policy": "never",
            }
        },
    }


async def test_real_codex_turn_produces_result(real_daemon):
    c = await _client(real_daemon)
    try:
        session = str(uuid.uuid4())
        await c.send(_open_codex(session))
        await c.wait_for(lambda e: e.get("type") == "blemeesd.opened", timeout=30.0)
        await c.send(
            {
                "type": "agent.user",
                "session_id": session,
                "message": {"role": "user", "content": "Say OK."},
            }
        )
        res = await c.wait_for(
            lambda e: e.get("type") == "agent.result" and e.get("session_id") == session,
            timeout=120.0,
        )
        assert res["subtype"] in {"success", "error", "interrupted"}
    finally:
        await c.close()


async def test_real_codex_context_across_two_turns(real_daemon):
    """Two-turn context test — single connection, single codex child.

    Resume across daemon-detach is intentionally NOT tested here: codex
    `tools/call codex-reply` rehydrates from the per-conversation
    rollout file, but that path is unstable in 0.125.x (a fresh
    `codex mcp-server` process called with a `threadId` from a prior
    process returns an empty success without rehydrating). The
    daemon-mock suite covers the cross-process resume routing
    (verifying we emit `codex-reply` with the cached threadId); we
    leave the actual context preservation to whichever side of the
    codex API stabilises first.
    """
    c = await _client(real_daemon)
    try:
        session = str(uuid.uuid4())
        await c.send(_open_codex(session))
        await c.wait_for(lambda e: e.get("type") == "blemeesd.opened", timeout=30.0)
        await c.send(
            {
                "type": "agent.user",
                "session_id": session,
                "message": {"role": "user", "content": "Remember the number 17."},
            }
        )
        await c.wait_for(
            lambda e: e.get("type") == "agent.result" and e.get("session_id") == session,
            timeout=120.0,
        )
        await c.send(
            {
                "type": "agent.user",
                "session_id": session,
                "message": {
                    "role": "user",
                    "content": "What number did I ask you to remember? Answer with just the number.",
                },
            }
        )
        collected = await c.wait_for(
            lambda e: e.get("type") == "agent.result" and e.get("session_id") == session,
            collect=True,
            timeout=120.0,
        )
        text = ""
        for evt in collected:
            if evt.get("type") == "agent.message":
                for block in evt.get("content", []) or evt.get("message", {}).get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text += block.get("text", "")
        assert "17" in text, (
            f"second turn lost context: text={text!r} "
            f"frames={[(e.get('type'), e.get('subtype')) for e in collected]}"
        )
    finally:
        await c.close()


async def test_real_codex_interrupt_then_continue(real_daemon):
    """Interrupt mid-stream → eventual `agent.result`.

    Codex's MCP server (0.125.x) responds to `notifications/cancelled`
    by completing the in-flight `tools/call` rather than aborting it
    early — so the post-interrupt `agent.result` arrives only after the
    underlying turn finishes naturally. We give it a generous budget
    and assert that:

      1. `blemeesd.interrupted{was_idle:false}` lands quickly,
      2. an `agent.result` eventually arrives (subtype is whatever
         Codex produces — `interrupted` if our cancel-flag wins the
         race, or `success` if the model simply finishes first),
      3. a follow-up turn still works on the same threadId.
    """
    c = await _client(real_daemon)
    try:
        session = str(uuid.uuid4())
        await c.send(_open_codex(session))
        await c.wait_for(lambda e: e.get("type") == "blemeesd.opened", timeout=30.0)
        await c.send(
            {
                "type": "agent.user",
                "session_id": session,
                "message": {
                    "role": "user",
                    "content": "Count slowly from 1 to 50, one number per line.",
                },
            }
        )
        await c.wait_for(lambda e: e.get("type") == "agent.delta", timeout=120.0)
        await c.send({"type": "blemeesd.interrupt", "session_id": session})
        ir = await c.wait_for(lambda e: e.get("type") == "blemeesd.interrupted", timeout=15.0)
        assert ir["was_idle"] is False
        # Codex 0.125.x signals the cancellation via a `turn_aborted`
        # event; the translator finalises the in-flight turn from that
        # event so we don't have to wait for the JSON-RPC response
        # (which Codex sometimes never sends after an abort).
        result = await c.wait_for(
            lambda e: e.get("type") == "agent.result" and e.get("session_id") == session,
            timeout=60.0,
        )
        assert result["subtype"] in {"success", "interrupted", "error"}
        # Subsequent turn still works on the same threadId.
        await c.send(
            {
                "type": "agent.user",
                "session_id": session,
                "message": {"role": "user", "content": "Say OK."},
            }
        )
        # Codex 0.125.x can take several minutes to send the terminal
        # JSON-RPC reply when the previous turn was aborted; events
        # for the aborted turn keep streaming for a while in parallel
        # with events for the new turn, and the daemon's reader
        # tolerates that interleaving.
        await c.wait_for(
            lambda e: e.get("type") == "agent.result" and e.get("session_id") == session,
            timeout=300.0,
        )
    finally:
        await c.close()
