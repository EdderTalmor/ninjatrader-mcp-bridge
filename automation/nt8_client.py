"""
NT8 Strategy Bridge Client  (FIXED)
====================================
Client library for WSL / Linux to talk to the Windows NT8 Bridge Server.
Matches the rewritten bridge server's response shape.

Usage:
  python nt8_client.py --health
  python nt8_client.py --compile --strategy MyStrategy.cs --name MyStrategy
  python nt8_client.py --run --strategy MyStrategy.cs --name MyStrategy \\
      --instrument "MES 06-26" --bar-type Minute --bar-value 5 \\
      --goal "ProfitFactor > 1.5"
  python nt8_client.py --results MyStrategy
"""
import os
import sys
import json
import time
import argparse

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)


WINDOWS_HOST = os.environ.get("NT8_HOST", "100.91.249.72")
WINDOWS_PORT = os.environ.get("NT8_PORT", "8787")
BASE_URL = f"http://{WINDOWS_HOST}:{WINDOWS_PORT}"


# ─── API ───────────────────────────────────────────────────────────────────

def health_check():
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        if r.status_code == 200:
            data = r.json()
            print(f"  [OK] Bridge: {data.get('status')} @ {data.get('time')}")
            print(f"       Strategies dir: {data.get('strategies_dir')}")
            print(f"       Output dir:     {data.get('output_dir')}")
            print(f"       Active jobs:    {data.get('active_jobs')}")
            return True
    except Exception as e:
        print(f"  [FAIL] Cannot reach bridge: {e}")
    return False


def compile_strategy(strategy_code, strategy_name, template_params=None):
    payload = {
        "strategy_code": strategy_code,
        "strategy_name": strategy_name,
    }
    if template_params:
        payload["template_params"] = template_params
    r = requests.post(f"{BASE_URL}/compile", json=payload, timeout=300)
    return r.json()


def run_strategy(strategy_code, strategy_name, instrument="MES 06-26",
                 bar_type="Minute", bar_value=5, date_from=None, date_to=None,
                 commission=None, slippage=None, template_params=None, goal=None,
                 wait=True):
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

    r = requests.post(f"{BASE_URL}/test-strategy", json=payload, timeout=30)
    submit = r.json()
    if "error" in submit:
        return submit

    if not wait:
        return submit

    # Poll for completion
    job_id = submit["job_id"]
    print(f"  [job] id={job_id}  polling...")
    while True:
        time.sleep(3)
        jr = requests.get(f"{BASE_URL}/job/{job_id}", timeout=10).json()
        status = jr.get("status")
        if status in ("done", "error"):
            return jr.get("result", jr)
        print(f"  [job] status={status}")


def get_results(strategy_name):
    r = requests.get(f"{BASE_URL}/results/{strategy_name}", timeout=10)
    return r.json()


# ─── Pretty printing ───────────────────────────────────────────────────────

def format_result(result):
    if "error" in result:
        return f"ERROR: {result['error']}"

    lines = []
    lines.append(f"Strategy: {result.get('strategy_name', '?')}")
    lines.append(f"Stage:    {result.get('stage', '?')}")
    lines.append(f"Success:  {result.get('success', False)}")

    cr = result.get("compile_result")
    if cr:
        lines.append("")
        lines.append("Compile:")
        lines.append(f"  success: {cr.get('success')}")
        if cr.get("errors"):
            for e in cr["errors"]:
                ln = f"Line {e['line']}: " if e.get("line") else ""
                lines.append(f"  {ln}{e['message']}")
        elif cr.get("message"):
            lines.append(f"  {cr['message']}")

    if result.get("summary_text"):
        lines.append("")
        lines.append("Backtest summary:")
        for line in result["summary_text"].splitlines():
            lines.append(f"  {line}")

    if result.get("vs_goal"):
        vg = result["vs_goal"]
        lines.append("")
        lines.append("Goal check:")
        for c in vg.get("checks", []):
            sym = "OK" if c["passed"] else "FAIL"
            lines.append(f"  [{sym}] {c['metric']} {c['op']} {c['target']} -> {c['actual']}")
        if vg.get("met"):
            lines.append("  >>> GOAL MET")
        elif vg.get("met") is False:
            lines.append("  >>> GOAL NOT MET — iterate again")

    return "\n".join(lines)


# ─── CLI ───────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="NT8 Strategy Bridge Client (fixed)")
    p.add_argument("--host", default=None, help="Override NT8 host")
    p.add_argument("--port", default=None, help="Override NT8 port")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="Check bridge health")

    pc = sub.add_parser("compile", help="Compile only")
    pc.add_argument("--strategy", "-s", required=True, help="Path to .cs file")
    pc.add_argument("--name", required=True)
    pc.add_argument("--params", help="JSON string of template params")

    pr = sub.add_parser("run", help="Compile + backtest")
    pr.add_argument("--strategy", "-s", required=True, help="Path to .cs file")
    pr.add_argument("--name", required=True)
    pr.add_argument("--instrument", default="MES 06-26")
    pr.add_argument("--bar-type", default="Minute")
    pr.add_argument("--bar-value", type=int, default=5)
    pr.add_argument("--date-from", default=None)
    pr.add_argument("--date-to", default=None)
    pr.add_argument("--commission", default=None)
    pr.add_argument("--slippage", default=None)
    pr.add_argument("--params", default=None)
    pr.add_argument("--goal", default=None)

    pg = sub.add_parser("results", help="Get latest results for a strategy")
    pg.add_argument("name")

    args = p.parse_args()

    global BASE_URL
    host = args.host or os.environ.get("NT8_HOST", "100.91.249.72")
    port = args.port or os.environ.get("NT8_PORT", "8787")
    BASE_URL = f"http://{host}:{port}"

    if args.cmd == "health":
        return 0 if health_check() else 1

    if args.cmd == "compile":
        with open(args.strategy) as f:
            code = f.read()
        params = json.loads(args.params) if args.params else None
        result = compile_strategy(code, args.name, params)
        print(format_result(result))
        return 0 if result.get("success") else 1

    if args.cmd == "run":
        with open(args.strategy) as f:
            code = f.read()
        params = json.loads(args.params) if args.params else None
        result = run_strategy(
            strategy_code=code, strategy_name=args.name,
            instrument=args.instrument, bar_type=args.bar_type,
            bar_value=args.bar_value, date_from=args.date_from, date_to=args.date_to,
            commission=args.commission, slippage=args.slippage,
            template_params=params, goal=args.goal, wait=True,
        )
        print(format_result(result))
        if result.get("stage") == "compile":
            return 2
        if not result.get("success"):
            return 3
        if result.get("vs_goal", {}).get("met") is False:
            return 4
        return 0

    if args.cmd == "results":
        result = get_results(args.name)
        print(format_result(result))
        return 0


if __name__ == "__main__":
    sys.exit(main())
