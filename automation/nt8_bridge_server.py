"""
NT8 Strategy Bridge Server
===========================
HTTP API running on Windows that accepts strategy code from WSL,
compiles + backtests in NinjaTrader 8, and returns results.

Run on Windows:
  python nt8_bridge_server.py

Then from WSL:
  curl -X POST http://100.91.249.72:8787/test-strategy \
    -H "Content-Type: application/json" \
    -d '{"strategy_code": "...", "instrument": "MES 06-26", "bar_type": "Minute", "bar_value": 5}'
"""
import os
import sys
import json
import time
import threading
import subprocess
from datetime import datetime

try:
    from flask import Flask, request, jsonify
except ImportError:
    print("ERROR: flask not installed. Run: python -m pip install flask")
    sys.exit(1)

# ─── Configuration ──────────────────────────────────────────────────────────

BRIDGE_PORT = 8787
BACKTESTER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nt8_backtester.py")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
STRATEGIES_DIR = os.path.expanduser(r"~\Documents\NinjaTrader 8\bin\Custom\Strategies")

# Thread lock to prevent concurrent backtests (NT8 can only do one at a time)
backtest_lock = threading.Lock()

app = Flask(__name__)


# ─── Background Job Runner ──────────────────────────────────────────────────

import threading
import uuid

jobs = {}  # job_id -> {"status": "running"|"done"|"error", "result": ..., "error": ...}
jobs_lock = threading.Lock()


def run_pipeline_background(data):
    """Run the full pipeline in a background thread."""
    job_id = str(uuid.uuid4())[:8]
    
    with jobs_lock:
        jobs[job_id] = {"status": "running", "started": datetime.now().isoformat()}
    
    def worker():
        try:
            result = run_full_pipeline(data)
            with jobs_lock:
                jobs[job_id] = {"status": "done", "result": result, "completed": datetime.now().isoformat()}
        except Exception as e:
            with jobs_lock:
                jobs[job_id] = {"status": "error", "error": str(e), "completed": datetime.now().isoformat()}
    
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return job_id


# ─── Routes ─────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    with jobs_lock:
        active = sum(1 for j in jobs.values() if j["status"] == "running")
    return jsonify({"status": "ok", "server": "nt8-bridge", "active_jobs": active, "time": datetime.now().isoformat()})


@app.route("/test-strategy", methods=["POST"])
def test_strategy():
    """
    Submit a strategy for testing. Returns immediately with a job_id.
    Poll /job/<job_id> for results.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body provided"}), 400
    
    if "strategy_code" not in data:
        return jsonify({"error": "strategy_code is required"}), 400
    
    job_id = run_pipeline_background(data)
    return jsonify({"job_id": job_id, "status": "submitted", "poll_url": f"/job/{job_id}"})


@app.route("/job/<job_id>", methods=["GET"])
def get_job(job_id):
    """Get the status/result of a submitted job."""
    with jobs_lock:
        job = jobs.get(job_id)
    
    if not job:
        return jsonify({"error": f"Job {job_id} not found"}), 404
    
    if job["status"] == "running":
        return jsonify({"job_id": job_id, "status": "running"})
    
    if job["status"] == "error":
        return jsonify({"job_id": job_id, "status": "error", "error": job["error"]})
    
    return jsonify({"job_id": job_id, "status": "done", "result": job["result"]})


@app.route("/compile", methods=["POST"])
def compile_only():
    """Compile only, do not run backtest."""
    data = request.get_json()
    if not data or "strategy_code" not in data:
        return jsonify({"err": "strategy_code is required"}), 400
    
    if not backtest_lock.acquire(blocking=False):
        return jsonify({"error": "Another operation is running."}), 429
    
    try:
        result = save_and_compile(data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        backtest_lock.release()


@app.route("/results/<strategy_name>", methods=["GET"])
def get_results(strategy_name):
    """Get the latest results CSV for a strategy."""
    strategy_dir = os.path.join(OUTPUT_DIR, strategy_name)
    if not os.path.exists(strategy_dir):
        return jsonify({"error": f"No results found for {strategy_name}"}), 404
    
    # Find latest run
    runs = sorted([d for d in os.listdir(strategy_dir) if os.path.isdir(os.path.join(strategy_dir, d))], reverse=True)
    if not runs:
        return jsonify({"error": "No run directories found"}), 404
    
    latest = os.path.join(strategy_dir, runs[0])
    summary_file = os.path.join(latest, "summary.csv")
    trades_file = os.path.join(latest, "trades.csv")
    metadata_file = os.path.join(latest, "metadata.json")
    
    result = {"strategy": strategy_name, "latest_run": runs[0], "files": {}}
    
    if os.path.exists(metadata_file):
        with open(metadata_file) as f:
            result["metadata"] = json.load(f)
    
    if os.path.exists(summary_file):
        with open(summary_file) as f:
            result["summary_csv"] = f.read()[:5000]  # limit size
    
    if os.path.exists(trades_file):
        with open(trades_file) as f:
            result["trades_csv"] = f.read()[:5000]
    
    return jsonify(result)


# ─── Pipeline ───────────────────────────────────────────────────────────────

def save_strategy_file(strategy_code, strategy_name):
    """Save .cs code to the NT8 Strategies folder."""
    os.makedirs(STRATEGIES_DIR, exist_ok=True)
    
    # Sanitize filename
    safe_name = "".join(c for c in strategy_name if c.isalnum() or c in "._- ")
    if not safe_name.endswith(".cs"):
        safe_name += ".cs"
    
    filepath = os.path.join(STRATEGIES_DIR, safe_name)
    
    # Remove old file if exists (to avoid SameFileError on copy)
    if os.path.exists(filepath):
        os.remove(filepath)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(strategy_code)
    
    return filepath, safe_name


def inject_parameters(strategy_code, params):
    """Replace strategy parameters in the .cs code.
    
    Replaces patterns like:
        [NinjaScriptProperty]
        public int Fast { get; set; }
    
    With the values from params dict.
    """
    if not params:
        return strategy_code
    
    import re
    
    for param_name, param_value in params.items():
        # Pattern: find property declarations and replace default values
        # Matches: public int PARAM_NAME { get; set; } = DEFAULT;
        # or:     public int PARAM_NAME = DEFAULT;
        
        # Try attribute-based property pattern
        pattern = rf'(\[NinjaScriptProperty\][^\n]*\n\s*public\s+\w+\s+{param_name}\s*\{{[^}}]*\}}\s*=\s*([^;]+);)'
        replacement = rf'\g<1>'.rsplit('=', 1)[0] + f'= {param_value};'
        
        new_code = re.sub(pattern, replacement, strategy_code)
        if new_code == strategy_code:
            # Try simpler pattern without attribute
            pattern2 = rf'(public\s+\w+\s+{param_name}\s*=\s*)([^;]+)(;)'
            new_code = re.sub(pattern2, rf'\g<1>{param_value}\3', strategy_code)
        
        strategy_code = new_code
    
    return strategy_code


def run_full_pipeline(data, backtest_only=False):
    """Execute the full compile + backtest + export pipeline."""
    
    strategy_code = data.get("strategy_code", "")
    strategy_name = data.get("strategy_name", f"AutoStrategy_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    instrument = data.get("instrument", "MES 06-26")
    bar_type = data.get("bar_type", "Minute")
    bar_value = data.get("bar_value", 5)
    date_from = data.get("date_from")
    date_to = data.get("date_to")
    commission = data.get("commission", "1.27")
    slippage = data.get("slippage", "1")
    template_params = data.get("template_params", {})
    
    result = {
        "strategy_name": strategy_name,
        "started": datetime.now().isoformat(),
        "steps": {}
    }
    
    # Step 0: Inject parameters if provided
    if template_params:
        strategy_code = inject_parameters(strategy_code, template_params)
        result["steps"]["inject_params"] = f"Applied {len(template_params)} parameters"
    
    # Step 1: Save file
    print(f"[*] Saving strategy: {strategy_name}")
    filepath, filename = save_strategy_file(strategy_code, strategy_name)
    result["steps"]["save"] = f"Saved to {filename}"
    
    # Step 2: Run backtester
    print(f"[*] Running backtester...")
    cmd = [
        sys.executable, BACKTESTER_SCRIPT,
        "-s", filepath,
        "--instrument", instrument,
        "--bar-type", bar_type,
        "--bar-value", str(bar_value),
        "--commission", commission,
        "--slippage", slippage,
    ]
    if backtest_only:
        cmd.append("-b")  # backtest only (skip compile, file already saved)
    if date_from:
        cmd.extend(["--date-from", date_from])
    if date_to:
        cmd.extend(["--date-to", date_to])
    
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max
            cwd=os.path.dirname(BACKTESTER_SCRIPT)
        )
        
        result["steps"]["backtester_exit_code"] = proc.returncode
        result["steps"]["stdout"] = proc.stdout[-2000:] if proc.stdout else ""
        result["steps"]["stderr"] = proc.stderr[-1000:] if proc.stderr else ""
        
        if proc.returncode != 0:
            result["success"] = False
            result["error"] = f"Backtester failed with exit code {proc.returncode}"
            return result
        
    except subprocess.TimeoutExpired:
        result["success"] = False
        result["error"] = "Backtest timed out after 600s"
        return result
    
    # Step 3: Read results from Strategy Analyzer XML logs
    print(f"[*] Reading results from Strategy Analyzer XML logs...")
    logs_dir = os.path.expanduser(r"~\Documents\NinjaTrader 8\strategyanalyzerlogs")
    
    # Wait up to 60 seconds for the log file to appear
    xml_file = None
    for attempt in range(12):
        if os.path.exists(logs_dir):
            files = sorted([f for f in os.listdir(logs_dir) if f.endswith(".xml")], reverse=True)
            if files:
                # Get the most recent file that was modified in the last 5 minutes
                xml_file_path = os.path.join(logs_dir, files[0])
                mtime = os.path.getmtime(xml_file_path)
                if time.time() - mtime < 300:  # modified in last 5 min
                    xml_file = xml_file_path
                    break
        time.sleep(5)
    
    if xml_file:
        # Parse the XML log
        import xml.etree.ElementTree as ET
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            
            # Extract key metrics from the XML
            metrics = {}
            for elem in root.iter():
                tag = elem.tag.lower()
                text = elem.text.strip() if elem.text else ""
                if text:
                    if "netprofit" in tag or "net profit" in tag:
                        metrics["net_profit"] = text
                    elif "profitfactor" in tag or "profit factor" in tag:
                        metrics["profit_factor"] = text
                    elif "totaltrades" in tag or "total trades" in tag or "trade count" in tag:
                        metrics["total_trades"] = text
                    elif "maxdrawdown" in tag or "max drawdown" in tag:
                        metrics["max_drawdown"] = text
                    elif "sharperatio" in tag or "sharpe ratio" in tag:
                        metrics["sharpe_ratio"] = text
                    elif "percentprofitable" in tag or "percent profitable" in tag:
                        metrics["win_rate"] = text
                    elif "grossprofit" in tag or "gross profit" in tag:
                        metrics["gross_profit"] = text
                    elif "grossloss" in tag or "gross loss" in tag:
                        metrics["gross_loss"] = text
                    elif "commission" in tag:
                        metrics["commission"] = text
                    elif "maxconsecutivelosers" in tag or "max consec losers" in tag:
                        metrics["max_consec_losers"] = text
                    elif "avgtrade" in tag or "avg trade" in tag:
                        metrics["avg_trade"] = text
            
            result["metrics"] = metrics
            result["xml_log"] = xml_file
            result["exported"] = True
            print(f"  [OK] Results extracted from XML log")
            for k, v in metrics.items():
                print(f"    {k}: {v}")
        except Exception as e:
            print(f"  [WARN] Failed to parse XML: {e}")
            result["xml_log"] = xml_file
    else:
        print(f"  [WARN] No recent XML log files found in {logs_dir}")
    
    result["success"] = True
    result["completed"] = datetime.now().isoformat()
    
    return result


def save_and_compile(data):
    """Save and compile only."""
    strategy_code = data.get("strategy_code", "")
    strategy_name = data.get("strategy_name", f"AutoStrategy_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    template_params = data.get("template_params", {})
    
    if template_params:
        strategy_code = inject_parameters(strategy_code, template_params)
    
    filepath, filename = save_strategy_file(strategy_code, strategy_name)
    
    # Run compile-only
    cmd = [sys.executable, BACKTESTER_SCRIPT, "-c", "-s", filepath]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                          cwd=os.path.dirname(BACKTESTER_SCRIPT))
    
    return {
        "strategy_name": strategy_name,
        "filename": filename,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-1000:],
        "stderr": proc.stderr[-500:],
        "success": proc.returncode == 0
    }


def parse_summary_csv(csv_content):
    """Extract key performance metrics from the summary CSV."""
    metrics = {}
    
    try:
        lines = csv_content.strip().split("\n")
        
        # Look for key metrics in the CSV
        for line in lines:
            line_lower = line.lower()
            
            if "net profit" in line_lower or "total net" in line_lower:
                # Extract the dollar value
                parts = line.split(",")
                for part in parts:
                    part = part.strip()
                    if "$" in part or "-" in part.replace(".", "").replace("-", "").isdigit():
                        metrics["net_profit"] = part
                        break
            
            if "win rate" in line_lower or "percent profitable" in line_lower:
                parts = line.split(",")
                for part in parts:
                    if "%" in part:
                        metrics["win_rate"] = part.strip()
                        break
            
            if "total trades" in line_lower or "total trade" in line_lower:
                parts = line.split(",")
                for part in parts:
                    part = part.strip()
                    if part.isdigit():
                        metrics["total_trades"] = int(part)
                        break
            
            if "drawdown" in line_lower and "max" in line_lower:
                parts = line.split(",")
                for part in parts:
                    if "$" in part:
                        metrics["max_drawdown"] = part.strip()
                        break
            
            if "sharpe" in line_lower:
                parts = line.split(",")
                metrics["sharpe_ratio"] = parts[-1].strip() if len(parts) > 1 else ""
            
            if "profit factor" in line_lower:
                parts = line.split(",")
                metrics["profit_factor"] = parts[-1].strip() if len(parts) > 1 else ""
    
    except Exception as e:
        metrics["parse_error"] = str(e)
    
    return metrics


# ─── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"NT8 Strategy Bridge Server")
    print(f"Port: {BRIDGE_PORT}")
    print(f"Strategies dir: {STRATEGIES_DIR}")
    print(f"Backtester: {BACKTESTER_SCRIPT}")
    print(f"{'='*60}\n")
    
    # Verify backtester exists
    if not os.path.exists(BACKTESTER_SCRIPT):
        print(f"[WARN] Backtester not found at: {BACKTESTER_SCRIPT}")
        print("[WARN] /test-strategy will fail until this path is correct.")
    
    print(f"Server starting on port {BRIDGE_PORT}...")
    print(f"Test with: curl http://localhost:{BRIDGE_PORT}/health\n")
    
    app.run(host="0.0.0.0", port=BRIDGE_PORT, debug=False, threaded=True)
