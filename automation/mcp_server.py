"""
NT8 MCP Server
===============
A Model Context Protocol (MCP) server that exposes NinjaTrader 8 strategy
development tools to AI agents (Claude Desktop, Cursor, etc.). This is the
actual "AI agent bridge" the project was supposed to be.

WHAT IS MCP?
------------
MCP is Anthropic's open protocol for letting AI agents call external tools.
An MCP server exposes a set of named tools; the AI agent calls them like
function calls. This file exposes the NT8 strategy development workflow as
those tools.

TOOLS EXPOSED
-------------
  health_check()                                  → is the bridge + NT8 reachable?
  list_templates()                                → show available strategy templates
  compile_strategy(strategy_code, strategy_name)  → compile only, return errors
  run_backtest(strategy_code, strategy_name,
               instrument, bar_type, bar_value,
               date_from, date_to,
               template_params, goal)             → full pipeline, returns metrics
  get_results(strategy_name)                      → fetch latest results for a strategy
  iterate_strategy(strategy_code, strategy_name,
                   iteration, previous_summary,
                   ai_reasoning, goal, ...)       → next iteration of the AI loop

The tools communicate with the local HTTP bridge server (nt8_bridge_server.py)
running on the Windows NT8 machine. You point this MCP server at that bridge
via env vars:
    NT8_BRIDGE_HOST=100.91.249.72   (Tailscale IP of the NT8 machine)
    NT8_BRIDGE_PORT=8787

USAGE
-----
  1. Start the bridge server on the NT8 machine (python nt8_bridge_server.py).
  2. Run this MCP server on the AI-agent machine:
         python mcp_server.py
     It speaks JSON-RPC over stdio, which is what Claude Desktop expects.
  3. Add to your Claude Desktop config (claude_desktop_config.json):

         "mcpServers": {
           "nt8": {
             "command": "python",
             "args": ["/path/to/mcp_server.py"],
             "env": {
               "NT8_BRIDGE_HOST": "100.91.249.72",
               "NT8_BRIDGE_PORT": "8787"
             }
           }
         }

  4. Restart Claude Desktop. The NT8 tools will appear as function calls.

DEPENDENCIES
------------
  pip install mcp requests
"""
import os
import sys
import json
import time
import asyncio
from typing import Any

import requests

try:
    from mcp.server import Server
    from mcp.server.models import InitializationOptions
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("ERROR: mcp not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)


# ─── Bridge client ─────────────────────────────────────────────────────────

BRIDGE_HOST = os.environ.get("NT8_BRIDGE_HOST", "100.91.249.72")
BRIDGE_PORT = os.environ.get("NT8_BRIDGE_PORT", "8787")
BRIDGE_URL = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"
BRIDGE_TIMEOUT = 30


def bridge_get(path, **kwargs):
    r = requests.get(f"{BRIDGE_URL}{path}", timeout=BRIDGE_TIMEOUT, **kwargs)
    return r.json()


def bridge_post(path, payload, timeout=None):
    r = requests.post(
        f"{BRIDGE_URL}{path}",
        json=payload,
        timeout=timeout or BRIDGE_TIMEOUT,
    )
    return r.json()


def bridge_wait_for_job(job_id, poll_interval=3, max_wait=900):
    """Poll /job/<id> until done/error. Returns the full job dict."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            job = bridge_get(f"/job/{job_id}")
        except Exception as e:
            yield {"_poll_error": str(e)}
            time.sleep(poll_interval)
            continue
        status = job.get("status")
        if status in ("done", "error"):
            return job
        yield job
        time.sleep(poll_interval)
    return {"status": "error", "error": f"Job {job_id} timed out after {max_wait}s"}


# ─── Tool implementations ──────────────────────────────────────────────────

def tool_health_check() -> dict:
    """Verify the bridge server is reachable and report NT8 paths."""
    try:
        return bridge_get("/health")
    except Exception as e:
        return {"status": "unreachable", "error": str(e), "bridge_url": BRIDGE_URL}


def tool_list_templates() -> dict:
    return bridge_get("/templates")


def tool_compile_strategy(strategy_code: str, strategy_name: str,
                          template_params: dict | None = None) -> dict:
    """Save + compile a strategy. Returns compile errors with line numbers."""
    payload = {
        "strategy_code": strategy_code,
        "strategy_name": strategy_name,
    }
    if template_params:
        payload["template_params"] = template_params
    return bridge_post("/compile", payload, timeout=300)


def tool_run_backtest(strategy_code: str, strategy_name: str,
                      instrument: str = "MES 06-26",
                      bar_type: str = "Minute",
                      bar_value: int = 5,
                      date_from: str | None = None,
                      date_to: str | None = None,
                      commission: str | None = None,
                      slippage: str | None = None,
                      template_params: dict | None = None,
                      goal: str | None = None,
                      wait: bool = True) -> dict:
    """Compile + backtest a strategy. By default waits for completion.

    Returns the final job result dict with:
        - compile_result (errors with line numbers if compile failed)
        - metrics / key_metrics (NT8 backtest output)
        - summary_text (AI-friendly text)
        - vs_goal (if `goal` was provided)
    """
    payload = {
        "strategy_code": strategy_code,
        "strategy_name": strategy_name,
        "instrument": instrument,
        "bar_type": bar_type,
        "bar_value": bar_value,
    }
    if date_from: payload["date_from"] = date_from
    if date_to: payload["date_to"] = date_to
    if commission: payload["commission"] = commission
    if slippage: payload["slippage"] = slippage
    if template_params: payload["template_params"] = template_params
    if goal: payload["goal"] = goal

    submit = bridge_post("/test-strategy", payload)
    if "error" in submit:
        return submit
    job_id = submit["job_id"]

    if not wait:
        return {"job_id": job_id, "poll_url": f"/job/{job_id}", "status": "submitted"}

    # Poll until done
    final = None
    for update in bridge_wait_for_job(job_id):
        final = update
    return final


def tool_get_results(strategy_name: str) -> dict:
    """Fetch the latest results for a previously-run strategy."""
    return bridge_get(f"/results/{strategy_name}")


def tool_iterate_strategy(strategy_code: str, strategy_name: str,
                          iteration: int,
                          previous_summary: str | None = None,
                          ai_reasoning: str | None = None,
                          instrument: str = "MES 06-26",
                          bar_type: str = "Minute",
                          bar_value: int = 5,
                          date_from: str | None = None,
                          date_to: str | None = None,
                          commission: str | None = None,
                          slippage: str | None = None,
                          template_params: dict | None = None,
                          goal: str | None = None,
                          wait: bool = True) -> dict:
    """Run one iteration of the AI strategy-development loop.

    The body is the same as `run_backtest`, plus:
        iteration:           which iteration number this is (1, 2, 3, ...)
        previous_summary:    the summary_text from the previous iteration
        ai_reasoning:        why you revised the code the way you did
                              (logged for traceability)
        goal:                e.g. "ProfitFactor > 1.5 AND TotalNumTrades >= 100"

    The bridge records all of this and returns the new result + a `vs_goal`
    block telling you whether the goal was met.
    """
    payload = {
        "strategy_code": strategy_code,
        "strategy_name": strategy_name,
        "iteration": iteration,
        "instrument": instrument,
        "bar_type": bar_type,
        "bar_value": bar_value,
    }
    if previous_summary: payload["previous_result_summary"] = previous_summary
    if ai_reasoning:     payload["ai_reasoning"] = ai_reasoning
    if date_from:        payload["date_from"] = date_from
    if date_to:          payload["date_to"] = date_to
    if commission:       payload["commission"] = commission
    if slippage:         payload["slippage"] = slippage
    if template_params:  payload["template_params"] = template_params
    if goal:             payload["goal"] = goal

    submit = bridge_post("/iterate", payload)
    if "error" in submit:
        return submit
    job_id = submit["job_id"]
    if not wait:
        return {"job_id": job_id, "poll_url": f"/job/{job_id}", "status": "submitted"}

    final = None
    for update in bridge_wait_for_job(job_id):
        final = update
    return final


# ─── MCP server wiring ─────────────────────────────────────────────────────

server = Server("nt8-mcp")


TOOLS = [
    Tool(
        name="health_check",
        description=(
            "Check if the NT8 bridge server on the Windows machine is reachable, "
            "and report the configured paths (strategies dir, output dir, NT8 log dir). "
            "Call this first to confirm the bridge is up."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="list_templates",
        description="List the built-in strategy templates available for code generation.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="compile_strategy",
        description=(
            "Save a .cs strategy to the NT8 Strategies folder and compile it in "
            "NinjaScript Editor. Returns a structured compile result with errors "
            "and line numbers if compilation failed. Use this when you only want "
            "to verify code compiles, without running a backtest."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "strategy_code": {"type": "string", "description": "Full .cs source code of the strategy"},
                "strategy_name": {"type": "string", "description": "PascalCase class name (also used as filename)"},
                "template_params": {
                    "type": "object",
                    "description": "Optional. Map of {{placeholder}} -> value substitutions to apply before saving.",
                },
            },
            "required": ["strategy_code", "strategy_name"],
        },
    ),
    Tool(
        name="run_backtest",
        description=(
            "Compile + backtest a strategy in NinjaTrader's Strategy Analyzer. "
            "Blocks until the backtest completes (up to 15 minutes). Returns "
            "the compile result, the parsed metrics (TotalNetProfit, ProfitFactor, "
            "TotalNumTrades, MaxDrawdown, SharpeRatio, etc.), and an AI-friendly "
            "summary text. If `goal` is provided, also returns a `vs_goal` block."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "strategy_code":   {"type": "string"},
                "strategy_name":   {"type": "string"},
                "instrument":      {"type": "string", "default": "MES 06-26"},
                "bar_type":        {"type": "string", "default": "Minute", "description": "Minute / Hour / Day / Tick"},
                "bar_value":       {"type": "integer", "default": 5},
                "date_from":       {"type": "string", "description": "MM/dd/yyyy"},
                "date_to":         {"type": "string", "description": "MM/dd/yyyy"},
                "commission":      {"type": "string"},
                "slippage":        {"type": "string"},
                "template_params": {"type": "object"},
                "goal": {
                    "type": "string",
                    "description": (
                        "Free-text goal like 'ProfitFactor > 1.5 AND TotalNumTrades >= 100'. "
                        "The bridge will parse simple METRIC op NUMBER clauses and report pass/fail."
                    ),
                },
                "wait": {"type": "boolean", "default": True, "description": "If false, returns job_id immediately."},
            },
            "required": ["strategy_code", "strategy_name"],
        },
    ),
    Tool(
        name="get_results",
        description="Fetch the latest results for a previously-run strategy.",
        inputSchema={
            "type": "object",
            "properties": {
                "strategy_name": {"type": "string"},
            },
            "required": ["strategy_name"],
        },
    ),
    Tool(
        name="iterate_strategy",
        description=(
            "Run one iteration of the AI strategy-development loop. Same as "
            "run_backtest, but also records the iteration number, the previous "
            "iteration's summary, and the AI's reasoning for what changed. "
            "Use this for iteration 2+ of the loop."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "strategy_code":     {"type": "string"},
                "strategy_name":     {"type": "string"},
                "iteration":         {"type": "integer", "description": "1, 2, 3, ..."},
                "previous_summary":  {"type": "string", "description": "The summary_text from the previous iteration."},
                "ai_reasoning":      {"type": "string", "description": "Why you revised the code the way you did. Logged for traceability."},
                "instrument":        {"type": "string", "default": "MES 06-26"},
                "bar_type":          {"type": "string", "default": "Minute"},
                "bar_value":         {"type": "integer", "default": 5},
                "date_from":         {"type": "string"},
                "date_to":           {"type": "string"},
                "commission":        {"type": "string"},
                "slippage":          {"type": "string"},
                "template_params":   {"type": "object"},
                "goal":              {"type": "string"},
                "wait":              {"type": "boolean", "default": True},
            },
            "required": ["strategy_code", "strategy_name", "iteration"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "health_check":
            result = tool_health_check()
        elif name == "list_templates":
            result = tool_list_templates()
        elif name == "compile_strategy":
            result = tool_compile_strategy(**arguments)
        elif name == "run_backtest":
            result = tool_run_backtest(**arguments)
        elif name == "get_results":
            result = tool_get_results(**arguments)
        elif name == "iterate_strategy":
            result = tool_iterate_strategy(**arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}

    # MCP requires we return text content
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


# ─── Main ──────────────────────────────────────────────────────────────────

async def main():
    # Print startup info to stderr (stdout is reserved for MCP protocol)
    print(f"[nt8-mcp] Bridge URL: {BRIDGE_URL}", file=sys.stderr)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="nt8-mcp",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=None,
                    experimental_capabilities=None,
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
