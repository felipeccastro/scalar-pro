"""MCP (Model Context Protocol) server for Scalar Pro — stdio transport.

Hand-rolled JSON-RPC 2.0 over stdin/stdout, per MCP's stdio transport spec
(https://modelcontextprotocol.io) — not the `mcp` pip package. That keeps
this in line with the app's zero-pip-dependency rule (see AGENTS.md /
requirements.txt): the protocol is just newline-delimited JSON-RPC, small
enough to implement directly with stdlib `json`.

It doesn't reimplement any tools — it exposes the ones ai.py already built
for the Ask-AI chat agent. `ai.TOOLS_SCHEMA` becomes the `tools/list` result
(its OpenAI-shaped `parameters` renamed to MCP's `inputSchema`, same JSON
Schema underneath) and `ai._execute_tool` is `tools/call`'s dispatcher —
same read tools (list_clients, search, get_attention, ...) and same write
tools (ai._MUTATING_TOOLS — every record type, not just Client/Task),
unchanged. This is why a new tool only ever gets added in ai.py: this file
has nothing of its own to keep in sync.

Confirmation: in the chat UI, `_agent_loop` pauses on a mutating tool for a
human Confirm/Cancel (see `_MUTATING_TOOLS` in ai.py). There's no equivalent
hook here — an MCP *host* (Claude Desktop, Claude Code, ...) already prompts
the person before it ever sends `tools/call`, so that prompt is this
transport's confirmation step. Mutating tools run immediately once called.

Actor: this is a single-tenant app with no session to resolve a user from,
so writes are attributed to the first registered account (the owner) — the
same convention seed.py uses for its own standalone `__main__` block.

Run directly — this is the entrypoint an MCP client launches as a
subprocess, e.g. in Claude Desktop's config:

    { "mcpServers": { "scalar-pro": {
        "command": "python3", "args": ["/absolute/path/to/pro/mcp_server.py"]
    } } }

stdin:  one JSON-RPC request/notification per line.
stdout: one JSON-RPC response per line, in the same order, notifications
        (no "id") get no reply. Must stay pure JSON-RPC — nothing else may
        ever print to stdout.
stderr: free for logging; never read by the client.
"""

from __future__ import annotations

import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Same vendoring trick as app.py: peewee/bottle live in vendor/ as plain .py
# files, not pip-installed, so this has to go on sys.path before anything
# below imports models/ai (which import peewee/bottle in turn).
sys.path.insert(0, os.path.join(BASE_DIR, "vendor"))


def _load_dotenv() -> None:
    """Same as app.py's _load_dotenv — duplicated rather than imported so
    this script has zero dependency on app.py (which also wires up every
    Bottle route/template on import; this only needs the DB + ai.py)."""
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ.setdefault(key, value)


_load_dotenv()

from models import User, db, ensure_schema  # noqa: E402

import ai  # noqa: E402

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "scalar-pro", "version": "1.0.0"}


def _actor() -> User | None:
    """Re-queried per call (cheap) rather than cached once at startup, so an
    owner registered after this process started is picked up without a
    restart."""
    return User.select().order_by(User.id).first()


def _tools_list(_params: dict) -> dict:
    return {
        "tools": [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "inputSchema": t["function"]["parameters"],
            }
            for t in ai.TOOLS_SCHEMA
        ]
    }


def _tools_call(params: dict) -> dict:
    name = params.get("name")
    args = params.get("arguments") or {}
    actor = _actor()
    # Read tools never touch `actor` — only the mutating tools do (see
    # ai._MUTATING_TOOLS) — so only a write needs to fail here for lack of one.
    if name in ai._MUTATING_TOOLS and actor is None:
        result = {"error": "No user registered yet — visit /setup in the app first."}
    else:
        result = ai._execute_tool(name, args, actor=actor)
    return {
        "content": [{"type": "text", "text": json.dumps(result)}],
        "isError": "error" in result,
    }


def _initialize(_params: dict) -> dict:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": SERVER_INFO,
    }


_METHODS = {
    "initialize": _initialize,
    "tools/list": _tools_list,
    "tools/call": _tools_call,
}


def _handle(msg: dict) -> dict | None:
    """One JSON-RPC request -> one response dict, or None for a
    notification ("id"-less, no reply expected — notifications/initialized
    is the only one a client sends us)."""
    msg_id = msg.get("id")
    method = msg.get("method", "")
    if msg_id is None or method.startswith("notifications/"):
        return None
    fn = _METHODS.get(method)
    if fn is None:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}
    try:
        result = fn(msg.get("params") or {})
    except Exception as e:
        # A bad tool call (bad args, DB error, ...) must not kill the loop —
        # every other stdio server process this client has open keeps running.
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": str(e)}}
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def main() -> None:
    ensure_schema()
    db.connect(reuse_if_open=True)
    print("Scalar Pro MCP server ready (stdio).", file=sys.stderr, flush=True)
    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                print(json.dumps(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {e}"}}
                ), flush=True)
                continue
            response = _handle(msg)
            if response is not None:
                print(json.dumps(response), flush=True)
    finally:
        if not db.is_closed():
            db.close()


if __name__ == "__main__":
    main()
