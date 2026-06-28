"""
NT8 Strategy Analyzer XML Parser
================================
Single source of truth for parsing NinjaTrader 8's Strategy Analyzer XML log
files. Used by both nt8_backtester.py and nt8_bridge_server.py so they no
longer diverge on parsing logic.

WHY THIS EXISTS
---------------
The original code had TWO different XML parsers:
  - nt8_bridge_server.py used substring matching ("netprofit" in tag) which
    matched stray elements like "<Commission>false</Commission>" — that's why
    half the output folders contain `{"commission": "false"}`.
  - nt8_backtester.py parsed the `SummaryPerformancesSerialize` element
    correctly (it's a `|`-separated, `;`-separated list of name;value;value;value).

This module consolidates on the *correct* approach.
"""
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime

_HOME = os.path.expanduser("~")
_XML_LOG_DIR_CANDIDATES = [
    os.path.join(_HOME, "OneDrive", "Documents", "NinjaTrader 8", "strategyanalyzerlogs"),
    os.path.join(_HOME, "Documents", "NinjaTrader 8", "strategyanalyzerlogs"),
]


def find_xml_log_dir():
    for p in _XML_LOG_DIR_CANDIDATES:
        if os.path.isdir(p):
            return p
    return None


def list_xml_logs():
    """Return list of (path, mtime) for all XML logs, newest first."""
    d = find_xml_log_dir()
    if not d:
        return []
    out = []
    for f in os.listdir(d):
        if not f.lower().endswith(".xml"):
            continue
        path = os.path.join(d, f)
        try:
            out.append((path, os.path.getmtime(path)))
        except OSError:
            pass
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def wait_for_fresh_xml(min_mtime=None, timeout=120, poll_interval=2.0):
    """Wait for an XML log file newer than `min_mtime` to appear.

    Args:
        min_mtime: float — only consider files modified after this epoch time.
                   If None, uses now() at call time.
        timeout: seconds to wait
        poll_interval: seconds between checks

    Returns:
        path to the fresh XML file, or None if timed out.
    """
    if min_mtime is None:
        min_mtime = time.time()

    deadline = time.time() + timeout
    while time.time() < deadline:
        for path, mtime in list_xml_logs():
            if mtime >= min_mtime:
                return path
        time.sleep(poll_interval)
    return None


# ─── Parser ────────────────────────────────────────────────────────────────

# Order of columns inside each `;`-separated entry of SummaryPerformancesSerialize.
# The first column is the metric name; the next three are All/Long/Short values.
# This order was determined empirically from real NT8 export files.
_ALL_TRADES_INDEX = 1


def parse_summary_performances(xml_path):
    """Parse a Strategy Analyzer XML log and return a metrics dict.

    Strategy Analyzer XML files contain a <SummaryPerformancesSerialize> element
    whose text is a pipe-separated list of metric rows, each `;`-separated:

        MetricName;AllValue;LongValue;ShortValue|MetricName;AllValue;...

    We extract MetricName -> AllValue into a flat dict. We also extract a few
    structured fields from sibling elements (strategy name, instrument, dates)
    when present.
    """
    metrics = {
        "_source_file": xml_path,
        "_source_mtime": datetime.fromtimestamp(os.path.getmtime(xml_path)).isoformat(),
    }

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError as e:
        metrics["_parse_error"] = f"XML parse error: {e}"
        return metrics
    except Exception as e:
        metrics["_parse_error"] = f"Failed to read {xml_path}: {e}"
        return metrics

    # Walk all elements looking for the summary block + a few well-known top-level
    # siblings
    for elem in root.iter():
        tag = elem.tag.strip()

        # 1) Main summary metrics block
        if tag == "SummaryPerformancesSerialize" and elem.text:
            text = elem.text.strip()
            entries = text.split("|")
            for entry in entries:
                parts = entry.split(";")
                if len(parts) < 2:
                    continue
                name = parts[0].strip()
                if not name:
                    continue
                # All trades value = parts[1] (parts[2] = Long, parts[3] = Short)
                value = parts[_ALL_TRADES_INDEX].strip() if len(parts) > _ALL_TRADES_INDEX else ""
                if value and value.lower() != "nan":
                    metrics[name] = value
                # Also store Long/Short breakdown if present
                if len(parts) > 2:
                    long_val = parts[2].strip()
                    if long_val and long_val.lower() != "nan":
                        metrics[f"{name}__Long"] = long_val
                if len(parts) > 3:
                    short_val = parts[3].strip()
                    if short_val and short_val.lower() != "nan":
                        metrics[f"{name}__Short"] = short_val

        # 2) Top-level metadata that lives as a direct child of <StrategyAnalyzer>
        #    or similar root. These are written as plain text children.
        elif tag in ("StrategyName", "Instrument", "BarsPeriod", "From", "To",
                     "StartTime", "EndTime", "BarType", "BarsPeriodType",
                     "BarsPeriodValue", "Account"):
            if elem.text and elem.text.strip():
                metrics[f"_{tag}"] = elem.text.strip()

    return metrics


# ─── Convenience: pull out the metrics that matter for AI iteration ────────

KEY_METRICS = [
    # The ones an AI agent typically looks at to decide whether to iterate
    "TotalNetProfit",
    "TotalNetProfitValue",  # currency-formatted raw value
    "ProfitFactor",
    "TotalNumTrades",
    "NumWinningTrades",
    "NumLosingTrades",
    "PercentProfitable",
    "MaxDrawdown",
    "SharpeRatio",
    "SortinoRatio",
    "AverageTrade",
    "AverageWinningTrade",
    "AverageLosingTrade",
    "RatioAvgWinAvgLoss",
    "Commission",
    "ProfitPerMonth",
    "RSquared",
    "UlcerIndex",
    "MaxConsecWinners",
    "MaxConsecLosers",
]


def extract_key_metrics(metrics):
    """Return a small, AI-friendly subset of the full metrics dict.

    Always includes the keys in KEY_METRICS if present, plus a derived
    'is_profitable' boolean.
    """
    out = {}
    for k in KEY_METRICS:
        if k in metrics:
            v = metrics[k]
            # Try to coerce numerics
            try:
                fv = float(v)
                out[k] = fv
            except (ValueError, TypeError):
                out[k] = v
    # Derived
    if "TotalNetProfitValue" in out:
        try:
            out["is_profitable"] = float(out["TotalNetProfitValue"]) > 0
        except (ValueError, TypeError):
            pass
    return out


def metrics_for_agent(metrics):
    """Return a compact, human/AI-readable text summary of the metrics.

    Example:
        TotalNetProfit: -101345.20  (NOT PROFITABLE)
        ProfitFactor:   0.94
        TotalTrades:    6270  (W:2162 / L:4108)
        WinRate:        34.48%
        MaxDrawdown:    -32902.27
        SharpeRatio:    -0.61
        AvgTrade:       -3.23
    """
    if "_parse_error" in metrics:
        return f"XML PARSE ERROR: {metrics['_parse_error']}"

    lines = []
    np_raw = metrics.get("TotalNetProfitValue") or metrics.get("TotalNetProfit", "")
    try:
        np_float = float(np_raw)
        profit_tag = "PROFITABLE" if np_float > 0 else "NOT PROFITABLE"
        lines.append(f"TotalNetProfit: {np_float:,.2f}  ({profit_tag})")
    except (ValueError, TypeError):
        lines.append(f"TotalNetProfit: {np_raw}")

    def fmt(k, suffix=""):
        v = metrics.get(k)
        if v is None:
            return f"{k}: N/A"
        try:
            return f"{k}: {float(v):,.2f}{suffix}"
        except (ValueError, TypeError):
            return f"{k}: {v}"

    lines.append(fmt("ProfitFactor"))
    wt = metrics.get("NumWinningTrades", "?")
    lt = metrics.get("NumLosingTrades", "?")
    tt = metrics.get("TotalNumTrades", "?")
    lines.append(f"TotalTrades: {tt}  (W:{wt} / L:{lt})")

    pp = metrics.get("PercentProfitable")
    if pp is not None:
        try:
            lines.append(f"WinRate: {float(pp)*100:.2f}%")
        except (ValueError, TypeError):
            lines.append(f"WinRate: {pp}")

    lines.append(fmt("MaxDrawdown"))
    lines.append(fmt("SharpeRatio"))
    lines.append(fmt("SortinoRatio"))
    lines.append(fmt("AverageTrade"))
    lines.append(fmt("AverageWinningTrade"))
    lines.append(fmt("AverageLosingTrade"))
    lines.append(fmt("ProfitPerMonth"))
    lines.append(fmt("Commission"))

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python nt8_xml_parser.py <path-to-strategyanalyzer.xml>")
        print("       python nt8_xml_parser.py --latest   (parse most recent XML)")
        sys.exit(1)
    if sys.argv[1] == "--latest":
        logs = list_xml_logs()
        if not logs:
            print("No XML logs found in", find_xml_log_dir())
            sys.exit(1)
        path = logs[0][0]
        print(f"Parsing latest: {path}")
    else:
        path = sys.argv[1]
    m = parse_summary_performances(path)
    print(metrics_for_agent(m))
    print("\n--- raw metrics dict ---")
    import json
    print(json.dumps(m, indent=2))
