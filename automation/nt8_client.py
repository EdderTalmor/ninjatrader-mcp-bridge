"""
NT8 Strategy Bridge Client
===========================
Client library for WSL to communicate with the Windows NT8 Bridge Server.

Usage from WSL:
  python nt8_client.py --strategy "code here" --name "MyStrategy"
  python nt8_client.py --results MyStrategy
"""
import os
import sys
import json
import argparse
import requests

# Default — will use TAILSCALE_IP env var or this fallback
WINDOWS_HOST = os.environ.get("NT8_HOST", "100.91.249.72")
WINDOWS_PORT = os.environ.get("NT8_PORT", "8787")
BASE_URL = f"http://{WINDOWS_HOST}:{WINDOWS_PORT}"


def health_check():
    """Check if the Windows bridge server is reachable."""
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        if r.status_code == 200:
            data = r.json()
            print(f"  [OK] Bridge server: {data.get('status')} @ {data.get('time')}")
            return True
    except Exception as e:
        print(f"  [FAIL] Cannot reach bridge server: {e}")
    return False


def test_strategy(strategy_code, strategy_name=None, instrument="MES 06-26",
                  bar_type="Minute", bar_value=5, date_from=None, date_to=None,
                  commission="1.27", slippage="1", template_params=None):
    """Submit a strategy for compilation + backtesting."""
    payload = {
        "strategy_code": strategy_code,
        "strategy_name": strategy_name or f"AutoStrategy",
        "instrument": instrument,
        "bar_type": bar_type,
        "bar_value": bar_value,
        "commission": commission,
        "slippage": slippage,
    }
    if date_from:
        payload["date_from"] = date_from
    if date_to:
        payload["date_to"] = date_to
    if template_params:
        payload["template_params"] = template_params
    
    print(f"  [*] Submitting strategy to {BASE_URL}...")
    try:
        r = requests.post(f"{BASE_URL}/test-strategy", json=payload, timeout=600)
        result = r.json()
        return result
    except requests.exceptions.Timeout:
        print("  [FAIL] Request timed out (>10 min)")
        return {"error": "timeout"}
    except Exception as e:
        print(f"  [FAIL] Request failed: {e}")
        return {"error": str(e)}


def get_results(strategy_name):
    """Get the latest results for a strategy."""
    try:
        r = requests.get(f"{BASE_URL}/results/{strategy_name}", timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def format_results(result):
    """Pretty-print results for human/AI consumption."""
    if "error" in result:
        return f"ERROR: {result['error']}"
    
    lines = []
    lines.append(f"Strategy: {result.get('strategy_name', 'unknown')}")
    lines.append(f"Success: {result.get('success', False)}")
    
    if result.get("metrics"):
        m = result["metrics"]
        lines.append(f"  Net Profit: {m.get('net_profit', 'N/A')}")
        lines.append(f"  Win Rate: {m.get('win_rate', 'N/A')}")
        lines.append(f"  Total Trades: {m.get('total_trades', 'N/A')}")
        lines.append(f"  Max Drawdown: {m.get('max_drawdown', 'N/A')}")
        lines.append(f"  Sharpe Ratio: {m.get('sharpe_ratio', 'N/A')}")
        lines.append(f"  Profit Factor: {m.get('profit_factor', 'N/A')}")
    
    if result.get("steps"):
        lines.append(f"Steps:")
        for k, v in result["steps"].items():
            lines.append(f"  {k}: {v[:100]}")
    
    return "\n".join(lines)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NT8 Strategy Bridge Client")
    parser.add_argument("--strategy", "-s", help="Path to .cs file or strategy code string")
    parser.add_argument("--code", "-c", help="Raw strategy code string")
    parser.add_argument("--name", help="Strategy name")
    parser.add_argument("--instrument", default="MES 06-26", help="Instrument")
    parser.add_argument("--bar-type", default="Minute", help="Bar period type")
    parser.add_argument("--bar-value", type=int, default=5, help="Bar period value")
    parser.add_argument("--date-from", default=None, help="Start date MM/DD/YYYY")
    parser.add_argument("--date-to", default=None, help="End date MM/DD/YYYY")
    parser.add_argument("--commission", default="1.27", help="Commission per RT")
    parser.add_argument("--slippage", default="1", help="Slippage in ticks")
    parser.add_argument("--params", default=None, help="JSON string of template params")
    parser.add_argument("--results", "-r", help="Get results for strategy name")
    parser.add_argument("--health", action="store_true", help="Check bridge health")
    parser.add_argument("--host", default=None, help="Override Windows host IP")
    parser.add_argument("--port", default=None, help="Override Windows port")
    
    args = parser.parse_args()
    
    global BASE_URL
    if args.host:
        host = args.host
    else:
        host = os.environ.get("NT8_HOST", "100.91.249.72")
    port = args.port or os.environ.get("NT8_PORT", "8787")
    BASE_URL = f"http://{host}:{port}"
    
    if args.health:
        return 0 if health_check() else 1
    
    if args.results:
        result = get_results(args.results)
        print(format_results(result))
        return 0
    
    # Get strategy code
    strategy_code = None
    if args.code:
        strategy_code = args.code
    elif args.strategy:
        if os.path.exists(args.strategy):
            with open(args.strategy) as f:
                strategy_code = f.read()
        else:
            strategy_code = args.strategy  # treat as raw code
    else:
        print("ERROR: Provide --strategy or --code")
        return 1
    
    # Parse template params
    template_params = None
    if args.params:
        template_params = json.loads(args.params)
    
    # Submit
    result = test_strategy(
        strategy_code=strategy_code,
        strategy_name=args.name,
        instrument=args.instrument,
        bar_type=args.bar_type,
        bar_value=args.bar_value,
        date_from=args.date_from,
        date_to=args.date_to,
        commission=args.commission,
        slippage=args.slippage,
        template_params=template_params,
    )
    
    print(format_results(result))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
