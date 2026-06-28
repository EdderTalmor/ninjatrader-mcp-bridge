"""
NT8 Strategy Iteration Loop CLI
================================
A command-line driver for the AI strategy-development loop, for users who
don't have Claude Desktop configured yet (or want to drive the loop from a
script / shell). Talks directly to the bridge server.

WORKFLOW
--------
  1. You write a .cs strategy file (or use a template).
  2. Run: python iterate.py --strategy my_strat.cs --goal "ProfitFactor > 1.5"
  3. The script:
       - compiles it
       - backtests it
       - prints the results
       - writes a JSON "iteration record" to ./iterations/<name>/iter_N.json
  4. You (or an LLM in another terminal) review the result, edit the .cs file
     (or pass --new-strategy revised.cs), and re-run with --iteration N+1.
  5. Repeat until the goal is met (or you give up).

This is the simplest possible "AI iteration loop" you can run without an MCP
client. For full AI-driven iteration, use mcp_server.py with Claude Desktop.

EXAMPLES
--------
  # First iteration
  python iterate.py \\
      --strategy MyStrategy.cs \\
      --name MyStrategy \\
      --instrument "MES 06-26" \\
      --bar-type Minute --bar-value 5 \\
      --date-from 01/01/2024 --date-to 12/31/2024 \\
      --goal "ProfitFactor > 1.5 AND TotalNumTrades >= 100"

  # Second iteration with revised code
  python iterate.py \\
      --strategy MyStrategy_v2.cs \\
      --name MyStrategy \\
      --iteration 2 \\
      --reasoning "Tightened stop loss; switched to ATR-based sizing" \\
      --goal "ProfitFactor > 1.5 AND TotalNumTrades >= 100"

  # Just compile-check without backtesting
  python iterate.py --strategy MyStrategy.cs --name MyStrategy --compile-only
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime

import requests

BRIDGE_HOST = os.environ.get("NT8_BRIDGE_HOST", "100.91.249.72")
BRIDGE_PORT = os.environ.get("NT8_BRIDGE_PORT", "8787")
BRIDGE_URL = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"

ITERATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iterations")


def bridge_get(path):
    return requests.get(f"{BRIDGE_URL}{path}", timeout=30).json()


def bridge_post(path, payload, timeout=900):
    return requests.post(f"{BRIDGE_URL}{path}", json=payload, timeout=timeout).json()


def wait_for_job(job_id, poll_interval=3, max_wait=900):
    """Poll the bridge until the job is done. Print progress to stderr."""
    deadline = time.time() + max_wait
    last_status = None
    while time.time() < deadline:
        try:
            job = bridge_get(f"/job/{job_id}")
        except Exception as e:
            print(f"[poll error] {e}", file=sys.stderr)
            time.sleep(poll_interval)
            continue
        status = job.get("status")
        if status != last_status:
            print(f"[job] status={status}", file=sys.stderr)
            last_status = status
        if status in ("done", "error"):
            return job
        time.sleep(poll_interval)
    print(f"[TIMEOUT] job {job_id} did not finish in {max_wait}s", file=sys.stderr)
    return {"status": "error", "error": "timeout"}


def print_summary(result):
    """Pretty-print the result for the human / AI in the terminal."""
    print("\n" + "=" * 60)
    if "compile_result" in result and result["compile_result"].get("success") is False:
        print("COMPILE FAILED — revise the code and try again.")
        print(result["compile_result"].get("message", ""))
        print("=" * 60)
        return

    print(f"Strategy: {result.get('strategy_name', '?')}")
    if result.get("iteration") is not None:
        print(f"Iteration: {result['iteration']}")

    summary = result.get("summary_text")
    if summary:
        print()
        print(summary)

    if result.get("vs_goal"):
        vg = result["vs_goal"]
        print()
        print("GOAL CHECK:")
        print(vg.get("text", "(no structured goals parsed)"))
        if vg.get("met"):
            print(">>> GOAL MET — you can stop iterating.")
        elif vg.get("met") is False:
            print(">>> GOAL NOT MET — iterate again.")

    print("=" * 60)


def save_iteration_record(name, iteration, payload, result):
    """Persist the iteration to ./iterations/<name>/iter_<N>.json for traceability."""
    out_dir = os.path.join(ITERATIONS_DIR, name)
    os.makedirs(out_dir, exist_ok=True)
    record = {
        "iteration": iteration,
        "timestamp": datetime.now().isoformat(),
        "request": payload,
        "result": result,
    }
    path = os.path.join(out_dir, f"iter_{iteration:03d}.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=2, default=str)
    print(f"\n[record] Saved iteration record → {path}", file=sys.stderr)


def main():
    p = argparse.ArgumentParser(description="NT8 Strategy Iteration Loop CLI")
    p.add_argument("--strategy", "-s", required=True, help="Path to .cs strategy file")
    p.add_argument("--name", required=True, help="Strategy name (PascalCase class name)")
    p.add_argument("--iteration", type=int, default=1, help="Iteration number (default: 1)")
    p.add_argument("--reasoning", help="Why you revised the code (logged for traceability)")
    p.add_argument("--instrument", default="MES 06-26")
    p.add_argument("--bar-type", default="Minute")
    p.add_argument("--bar-value", type=int, default=5)
    p.add_argument("--date-from", help="MM/dd/yyyy")
    p.add_argument("--date-to", help="MM/dd/yyyy")
    p.add_argument("--commission")
    p.add_argument("--slippage")
    p.add_argument("--goal", help="Free-text goal like 'ProfitFactor > 1.5 AND TotalNumTrades >= 100'")
    p.add_argument("--compile-only", action="store_true", help="Compile only, don't backtest")
    p.add_argument("--previous-summary", help="The summary text from the previous iteration (for iteration 2+)")
    p.add_argument("--params", help="JSON string of {{placeholder}}->value substitutions")
    args = p.parse_args()

    # Read the strategy code
    if not os.path.exists(args.strategy):
        print(f"ERROR: strategy file not found: {args.strategy}", file=sys.stderr)
        return 1
    with open(args.strategy, "r", encoding="utf-8") as f:
        strategy_code = f.read()

    # Parse template params
    template_params = None
    if args.params:
        template_params = json.loads(args.params)

    # Health check first
    print(f"[bridge] {BRIDGE_URL}", file=sys.stderr)
    try:
        health = bridge_get("/health")
        print(f"[bridge] status={health.get('status')}  active_jobs={health.get('active_jobs')}", file=sys.stderr)
    except Exception as e:
        print(f"[bridge] UNREACHABLE: {e}", file=sys.stderr)
        return 2

    # Compile-only path
    if args.compile_only:
        payload = {
            "strategy_code": strategy_code,
            "strategy_name": args.name,
        }
        if template_params:
            payload["template_params"] = template_params
        result = bridge_post("/compile", payload, timeout=300)
        print(json.dumps(result, indent=2))
        return 0 if result.get("success") else 3

    # Full pipeline via /iterate
    payload = {
        "strategy_code": strategy_code,
        "strategy_name": args.name,
        "iteration": args.iteration,
        "instrument": args.instrument,
        "bar_type": args.bar_type,
        "bar_value": args.bar_value,
    }
    if args.date_from: payload["date_from"] = args.date_from
    if args.date_to: payload["date_to"] = args.date_to
    if args.commission: payload["commission"] = args.commission
    if args.slippage: payload["slippage"] = args.slippage
    if template_params: payload["template_params"] = template_params
    if args.goal: payload["goal"] = args.goal
    if args.reasoning: payload["ai_reasoning"] = args.reasoning
    if args.previous_summary: payload["previous_result_summary"] = args.previous_summary

    submit = bridge_post("/iterate", payload)
    if "error" in submit:
        print(f"[ERROR] bridge rejected submission: {submit['error']}", file=sys.stderr)
        return 4

    job_id = submit["job_id"]
    print(f"[bridge] job_id={job_id}  iteration={args.iteration}", file=sys.stderr)

    final = wait_for_job(job_id)
    result = final.get("result", final)

    print_summary(result)
    save_iteration_record(args.name, args.iteration, payload, result)

    # Exit code: 0 if goal met (or no goal), 1 if compile failed, 2 if backtest failed
    if result.get("stage") == "compile":
        return 1
    if result.get("stage") == "backtest" or result.get("stage") == "timeout":
        return 2
    if result.get("vs_goal", {}).get("met") is False:
        return 5  # goal not met — caller may want to iterate again
    return 0


if __name__ == "__main__":
    sys.exit(main())
