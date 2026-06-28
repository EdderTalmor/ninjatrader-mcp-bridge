"""
NT8 Strategy Bridge Server  (FIXED)
===================================
HTTP API running on Windows. Accepts strategy code from WSL / an MCP server /
an AI agent, compiles + backtests in NinjaTrader 8, and returns results.

This is the rewritten version. Key differences from the original:

  * Real compile detection — the /compile and /test-strategy endpoints now
    return actual compile errors (with line numbers) read from NT8's log file,
    instead of silently assuming success on timeout.

  * Forced XML save — after a backtest completes, we click the Save button in
    the Strategy Analyzer so NT8 actually writes the XML log file. The original
    polled for an XML file to "appear" — which it never does without Save —
    and ended up reading stale or unrelated XML.

  * Single source of truth for XML parsing — nt8_xml_parser.py is used by both
    the backtester and this server.

  * New /iterate endpoint — accepts the previous results + an AI-supplied
    revised strategy and runs the loop again. The response is structured so an
    AI agent can decide whether to keep iterating or stop.

  * /jobs list endpoint — see what's running.

Run on Windows:
    python nt8_bridge_server.py

Then from WSL:
    curl -X POST http://100.91.249.72:8787/test-strategy \\
      -H "Content-Type: application/json" \\
      -d '{"strategy_code": "...", "strategy_name": "MyStrat",
           "instrument": "MES 06-26", "bar_type": "Minute", "bar_value": 5}'
"""
import os
import sys
import json
import time
import threading
import subprocess
import uuid
from datetime import datetime

try:
    from flask import Flask, request, jsonify
except ImportError:
    print("ERROR: flask not installed. Run: python -m pip install flask")
    sys.exit(1)

# Same-package imports — make sure local modules import cleanly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nt8_compile_checker
import nt8_xml_parser

# ─── Configuration ──────────────────────────────────────────────────────────

BRIDGE_PORT = int(os.environ.get("NT8_BRIDGE_PORT", "8787"))
BACKTESTER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nt8_backtester.py")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
STRATEGIES_DIR = os.path.join(
    os.path.expanduser("~"), "OneDrive", "Documents", "NinjaTrader 8", "bin", "Custom", "Strategies"
)
if not os.path.exists(STRATEGIES_DIR):
    STRATEGIES_DIR = os.path.expanduser(r"~\Documents\NinjaTrader 8\bin\Custom\Strategies")

# NT8 can only do one compile/backtest at a time — serialize them.
backtest_lock = threading.Lock()

app = Flask(__name__)


# ─── Background job runner ─────────────────────────────────────────────────

jobs = {}  # job_id -> dict
jobs_lock = threading.Lock()


def run_pipeline_background(data, kind="full"):
    """Run the pipeline in a background thread. Returns job_id."""
    job_id = str(uuid.uuid4())[:8]
    with jobs_lock:
        jobs[job_id] = {
            "status": "running",
            "kind": kind,
            "started": datetime.now().isoformat(),
            "request_summary": {
                k: v for k, v in data.items()
                if k in ("strategy_name", "instrument", "bar_type", "bar_value",
                         "date_from", "date_to", "iteration")
            },
        }

    def worker():
        try:
            if kind == "compile":
                result = save_and_compile(data)
            else:
                result = run_full_pipeline(data)
            with jobs_lock:
                jobs[job_id].update({
                    "status": "done" if result.get("success") else "error",
                    "result": result,
                    "completed": datetime.now().isoformat(),
                })
        except Exception as e:
            with jobs_lock:
                jobs[job_id].update({
                    "status": "error",
                    "error": str(e),
                    "completed": datetime.now().isoformat(),
                })

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return job_id


# ─── Routes ────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    with jobs_lock:
        active = [jid for jid, j in jobs.items() if j["status"] == "running"]
    return jsonify({
        "status": "ok",
        "server": "nt8-bridge (fixed)",
        "active_jobs": active,
        "strategies_dir": STRATEGIES_DIR,
        "output_dir": OUTPUT_DIR,
        "time": datetime.now().isoformat(),
    })


@app.route("/jobs", methods=["GET"])
def list_jobs():
    with jobs_lock:
        return jsonify({"jobs": [
            {"job_id": jid, **{k: v for k, v in j.items() if k != "result"}}
            for jid, j in jobs.items()
        ]})


@app.route("/job/<job_id>", methods=["GET"])
def get_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": f"Job {job_id} not found"}), 404
    return jsonify({"job_id": job_id, **job})


@app.route("/compile", methods=["POST"])
def compile_only():
    """Save + compile a strategy. Returns compile errors with line numbers."""
    data = request.get_json() or {}
    if "strategy_code" not in data:
        return jsonify({"error": "strategy_code is required"}), 400

    if not backtest_lock.acquire(blocking=False):
        return jsonify({"error": "Another compile/backtest is running."}), 429

    try:
        result = save_and_compile(data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        backtest_lock.release()


@app.route("/test-strategy", methods=["POST"])
def test_strategy():
    """Submit a strategy for compile + backtest. Returns job_id immediately."""
    data = request.get_json() or {}
    if "strategy_code" not in data:
        return jsonify({"error": "strategy_code is required"}), 400

    job_id = run_pipeline_background(data, kind="full")
    return jsonify({
        "job_id": job_id,
        "status": "submitted",
        "poll_url": f"/job/{job_id}",
    })


@app.route("/results/<strategy_name>", methods=["GET"])
def get_results(strategy_name):
    """Return the latest metrics + AI summary for a strategy."""
    strategy_dir = os.path.join(OUTPUT_DIR, strategy_name)
    if not os.path.exists(strategy_dir):
        return jsonify({"error": f"No results found for {strategy_name}"}), 404

    out = {"strategy": strategy_name}

    metrics_path = os.path.join(strategy_dir, "metrics.json")
    if os.path.exists(metrics_path):
        with open(metrics_path) as f:
            out["metrics"] = json.load(f)

    key_path = os.path.join(strategy_dir, "key_metrics.json")
    if os.path.exists(key_path):
        with open(key_path) as f:
            out["key_metrics"] = json.load(f)

    summary_path = os.path.join(strategy_dir, "summary.txt")
    if os.path.exists(summary_path):
        with open(summary_path) as f:
            out["summary_text"] = f.read()

    compile_path = os.path.join(strategy_dir, "compile_result.json")
    if os.path.exists(compile_path):
        with open(compile_path) as f:
            out["compile_result"] = json.load(f)

    return jsonify(out)


@app.route("/iterate", methods=["POST"])
def iterate():
    """Run one iteration of the AI loop.

    Body:
        {
          "strategy_code": "<revised .cs>",
          "strategy_name": "MACrossover",
          "iteration": 2,
          "previous_result_summary": "TotalNetProfit: -101345.20 (NOT PROFITABLE) ...",
          "ai_reasoning": "Reduced Fast from 10 to 5 to make signals faster; added trend filter",
          "instrument": "MES 06-26",
          "bar_type": "Minute", "bar_value": 5,
          "date_from": "...", "date_to": "...",
          "goal": "Achieve ProfitFactor > 1.5 with at least 100 trades"
        }

    Returns a job_id; poll /job/<job_id>. The job's `result` will include:
        - compile_result (errors with line numbers if compile failed)
        - metrics (if backtest ran)
        - summary_text (AI-friendly text)
        - vs_goal (whether the goal was met, if a goal was provided)
    """
    data = request.get_json() or {}
    required = ["strategy_code", "strategy_name"]
    for r in required:
        if r not in data:
            return jsonify({"error": f"{r} is required"}), 400

    job_id = run_pipeline_background(data, kind="full")
    return jsonify({
        "job_id": job_id,
        "status": "submitted",
        "iteration": data.get("iteration", 0),
        "poll_url": f"/job/{job_id}",
    })


@app.route("/templates", methods=["GET"])
def list_templates():
    """List available strategy templates (from strategy_templates.py)."""
    try:
        import strategy_templates
        return jsonify({"templates": list(strategy_templates.TEMPLATES.keys()) if hasattr(strategy_templates, 'TEMPLATES')
                        else ["ma_crossover", "rsi_mean_reversion", "breakout", "dual_sma_filter"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Pipeline ──────────────────────────────────────────────────────────────

def save_strategy_file(strategy_code, strategy_name):
    """Save .cs code to the NT8 Strategies folder."""
    os.makedirs(STRATEGIES_DIR, exist_ok=True)
    safe_name = "".join(c for c in strategy_name if c.isalnum() or c in "._-")
    if not safe_name:
        safe_name = "AutoStrategy"
    if not safe_name.endswith(".cs"):
        safe_name += ".cs"
    filepath = os.path.join(STRATEGIES_DIR, safe_name)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception:
            pass
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(strategy_code)
    return filepath, safe_name


def save_and_compile(data):
    """Save the strategy, compile it, and return structured result."""
    strategy_code = data.get("strategy_code", "")
    strategy_name = data.get("strategy_name", f"AutoStrategy_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    template_params = data.get("template_params", {})

    if template_params:
        strategy_code = inject_parameters(strategy_code, template_params)

    filepath, filename = save_strategy_file(strategy_code, strategy_name)
    base_name = os.path.splitext(filename)[0]

    # Run compile-only via backtester (no backtest)
    cmd = [sys.executable, BACKTESTER_SCRIPT, "-c", "-s", filepath]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=180,
        cwd=os.path.dirname(BACKTESTER_SCRIPT),
    )

    # Read the compile_result.json that the backtester wrote
    result_dir = os.path.join(OUTPUT_DIR, base_name)
    compile_result_path = os.path.join(result_dir, "compile_result.json")
    compile_result = {}
    if os.path.exists(compile_result_path):
        with open(compile_result_path) as f:
            compile_result = json.load(f)
    else:
        # Backtester failed before writing the result file
        compile_result = {
            "success": None,
            "message": "Backtester exited before writing compile_result.json",
            "stdout_tail": proc.stdout[-2000:] if proc.stdout else "",
            "stderr_tail": proc.stderr[-1000:] if proc.stderr else "",
            "exit_code": proc.returncode,
        }

    return {
        "strategy_name": base_name,
        "filename": filename,
        "filepath": filepath,
        "compile_result": compile_result,
        "success": compile_result.get("success") is True,
    }


def inject_parameters(strategy_code, params):
    """Replace {{key}} placeholders in the code with values from `params`."""
    if not params:
        return strategy_code
    for k, v in params.items():
        strategy_code = strategy_code.replace("{{" + k + "}}", str(v))
    return strategy_code


def run_full_pipeline(data, backtest_only=False):
    """Full pipeline: save → compile → backtest → parse results."""
    strategy_code = data.get("strategy_code", "")
    strategy_name = data.get("strategy_name", f"AutoStrategy_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    instrument = data.get("instrument", "MES 06-26")
    bar_type = data.get("bar_type", "Minute")
    bar_value = data.get("bar_value", 5)
    date_from = data.get("date_from")
    date_to = data.get("date_to")
    commission = data.get("commission")
    slippage = data.get("slippage")
    template_params = data.get("template_params", {})
    goal = data.get("goal")

    result = {
        "strategy_name": strategy_name,
        "iteration": data.get("iteration"),
        "started": datetime.now().isoformat(),
        "steps": {},
    }

    if template_params:
        strategy_code = inject_parameters(strategy_code, template_params)
        result["steps"]["inject_params"] = f"Applied {len(template_params)} parameters"

    # Save
    print(f"[*] Saving strategy: {strategy_name}")
    filepath, filename = save_strategy_file(strategy_code, strategy_name)
    result["steps"]["save"] = f"Saved to {filename}"

    # Build backtester command
    cmd = [
        sys.executable, BACKTESTER_SCRIPT,
        "-s", filepath,
        "--instrument", instrument,
        "--bar-type", bar_type,
        "--bar-value", str(bar_value),
    ]
    if backtest_only:
        cmd.append("-b")
    if date_from:
        cmd.extend(["--date-from", date_from])
    if date_to:
        cmd.extend(["--date-to", date_to])
    if commission:
        cmd.extend(["--commission", str(commission)])
    if slippage:
        cmd.extend(["--slippage", str(slippage)])

    print(f"[*] Running backtester: {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=900,
            cwd=os.path.dirname(BACKTESTER_SCRIPT),
        )
        result["steps"]["backtester_exit_code"] = proc.returncode
        result["steps"]["stdout_tail"] = (proc.stdout or "")[-3000:]
        result["steps"]["stderr_tail"] = (proc.stderr or "")[-1500:]

        # Distinct exit code 2 = compile failed (per the fixed backtester)
        if proc.returncode == 2:
            result["success"] = False
            result["stage"] = "compile"
            # Pull compile errors from the saved compile_result.json
            cr_path = os.path.join(OUTPUT_DIR, strategy_name, "compile_result.json")
            if os.path.exists(cr_path):
                with open(cr_path) as f:
                    result["compile_result"] = json.load(f)
            return result

        if proc.returncode != 0:
            result["success"] = False
            result["stage"] = "backtest"
            result["error"] = f"Backtester exited with code {proc.returncode}"
            return result

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["stage"] = "timeout"
        result["error"] = "Backtester timed out after 900s"
        return result

    # Read parsed metrics
    metrics_path = os.path.join(OUTPUT_DIR, strategy_name, "metrics.json")
    key_path = os.path.join(OUTPUT_DIR, strategy_name, "key_metrics.json")
    summary_path = os.path.join(OUTPUT_DIR, strategy_name, "summary.txt")
    compile_path = os.path.join(OUTPUT_DIR, strategy_name, "compile_result.json")

    if os.path.exists(compile_path):
        with open(compile_path) as f:
            result["compile_result"] = json.load(f)

    if os.path.exists(metrics_path):
        with open(metrics_path) as f:
            result["metrics"] = json.load(f)

    if os.path.exists(key_path):
        with open(key_path) as f:
            result["key_metrics"] = json.load(f)

    if os.path.exists(summary_path):
        with open(summary_path) as f:
            result["summary_text"] = f.read()

    # Goal check
    if goal and "key_metrics" in result:
        result["vs_goal"] = check_goal(result["key_metrics"], goal)

    result["success"] = True
    result["stage"] = "complete"
    result["completed"] = datetime.now().isoformat()
    return result


def check_goal(key_metrics, goal):
    """Very simple goal evaluator. `goal` is a free-text string.

    We do best-effort numeric extraction:
      - "ProfitFactor > 1.5" → check if key_metrics['ProfitFactor'] > 1.5
      - "TotalNetProfitValue > 0" → check profitable
      - "TotalNumTrades >= 100" → check trade count

    Returns: dict with `met: bool`, `checks: list[dict]`, `text: str`.
    """
    import re
    checks = []
    # Pattern: METRIC_NAME (>|>=|<|<=|==) NUMBER
    for m in re.finditer(r"(\w+)\s*(>=|<=|==|>|<)\s*([\d.]+)", goal):
        name, op, num = m.group(1), m.group(2), float(m.group(3))
        actual = key_metrics.get(name)
        if actual is None:
            checks.append({"metric": name, "op": op, "target": num, "actual": None, "passed": False, "reason": "metric not found"})
            continue
        try:
            actual_f = float(actual)
        except (ValueError, TypeError):
            checks.append({"metric": name, "op": op, "target": num, "actual": actual, "passed": False, "reason": "non-numeric"})
            continue
        passed = {
            ">":  actual_f > num,
            ">=": actual_f >= num,
            "<":  actual_f < num,
            "<=": actual_f <= num,
            "==": abs(actual_f - num) < 1e-9,
        }[op]
        checks.append({"metric": name, "op": op, "target": num, "actual": actual_f, "passed": passed})

    met = all(c["passed"] for c in checks) if checks else None
    text_lines = []
    for c in checks:
        sym = "✓" if c["passed"] else "✗"
        text_lines.append(f"  {sym} {c['metric']} {c['op']} {c['target']} → actual {c['actual']}")
    return {
        "met": met,
        "checks": checks,
        "text": "\n".join(text_lines) if text_lines else "No structured goals parsed.",
    }


# ─── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"NT8 Strategy Bridge Server (FIXED)")
    print(f"Port: {BRIDGE_PORT}")
    print(f"Strategies dir: {STRATEGIES_DIR}")
    print(f"Output dir:     {OUTPUT_DIR}")
    print(f"Backtester:     {BACKTESTER_SCRIPT}")
    print(f"{'='*60}\n")

    if not os.path.exists(BACKTESTER_SCRIPT):
        print(f"[WARN] Backtester not found at: {BACKTESTER_SCRIPT}")

    # Show where compile logs are expected
    log_dir = nt8_compile_checker.find_log_dir()
    print(f"NT8 log dir:    {log_dir or '(NOT FOUND — compile detection will fail)'}")
    xml_dir = nt8_xml_parser.find_xml_log_dir()
    print(f"SA XML log dir: {xml_dir or '(NOT FOUND — XML results reading will fail)'}")

    print(f"\nServer starting on port {BRIDGE_PORT}...")
    print(f"Test with: curl http://localhost:{BRIDGE_PORT}/health\n")
    app.run(host="0.0.0.0", port=BRIDGE_PORT, debug=False, threaded=True)
