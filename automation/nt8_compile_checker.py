"""
NT8 Compile Checker
====================
Reads NinjaTrader 8's actual on-disk log to determine compile success/failure
with exact line numbers and error messages.

WHY THIS EXISTS
---------------
The old `wait_for_compilation_complete()` polled the NinjaScript Editor's UI for
status text, and on timeout it *assumed success*. That meant any strategy that
failed to compile (wrong namespace, missing reference, typo) was reported as
"compiled OK", the backtester then tried to run it, the Strategy Analyzer
silently fell back to the previous build, and you got back the *old* strategy's
metrics — not the new one's. The AI agent had no idea its code didn't compile,
so it couldn't iterate.

NT8 writes real compile output to:
    Documents\\NinjaTrader 8\\log\\<YYYY-MM-DD>.txt
    (or under OneDrive\\Documents\\... if OneDrive redirection is active)

Each compile produces lines like:
    Info NinjaTrader.NinjaScript.NinjaScriptBase Compile time error: ...
    Error on barUpdate ... line 42 ...
    NS auto compile successful ...

This module reads that log file, finds the most recent compile block, and
returns a structured result the AI agent can act on.
"""
import os
import re
import time
from datetime import datetime

# NT8 log directory (handles OneDrive redirection)
_HOME = os.path.expanduser("~")
_LOG_DIR_CANDIDATES = [
    os.path.join(_HOME, "OneDrive", "Documents", "NinjaTrader 8", "log"),
    os.path.join(_HOME, "Documents", "NinjaTrader 8", "log"),
]


def find_log_dir():
    """Return the first existing NT8 log directory."""
    for p in _LOG_DIR_CANDIDATES:
        if os.path.isdir(p):
            return p
    return None


def get_today_log_file():
    """Return the path to today's NT8 log file (created on demand by NT8)."""
    log_dir = find_log_dir()
    if not log_dir:
        return None
    fname = datetime.now().strftime("%Y-%m-%d") + ".txt"
    return os.path.join(log_dir, fname)


def _read_tail(path, max_bytes=200_000):
    """Read the last N bytes of a file (logs can be megabytes)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


# Regex patterns NT8 uses in its log
_COMPILE_SUCCESS_PATTERNS = [
    re.compile(r"\bNS auto compile successful\b", re.I),
    re.compile(r"\bCompilation succeeded\b", re.I),
    re.compile(r"\bBuild succeeded\b", re.I),
    # NT8 logs a "Code::Compile" entry on success
    re.compile(r"Code::Compile.*?completed", re.I),
]

_COMPILE_ERROR_PATTERNS = [
    # "Error on line N: <msg>" — NT8's own log format
    re.compile(
        r"Error\s+on\s+line\s+(\d+)\s*:\s*(.+?)(?:\n|$)",
        re.I | re.S,
    ),
    # "Error CSnnnn: <msg> (line N)" or "Error CSnnnn: <msg>"
    re.compile(
        r"error\s+cs\d{4}:\s*(.+?)(?:\s+\(line\s*(\d+)\))?(?:\n|$)",
        re.I | re.S,
    ),
    # NS auto compile failed with: <msg> ... line <n> ...
    re.compile(
        r"NS auto compile failed.*?(?:line\s*(\d+))?.*?[:\-]\s*(.+?)(?:\n|$)",
        re.I | re.S,
    ),
    # "Compile time error:" prefix used in many NT8 errors
    re.compile(
        r"Compile time error:?\s*(.+?)(?:\s+line\s*(\d+))?(?:\n|$)",
        re.I | re.S,
    ),
    # Generic "Error ..." free-form (last resort; must have a non-empty message)
    re.compile(r"^\s*Error\s+\d*[:\-]?\s*(.+?)(?:\n|$)", re.I | re.M),
]

# Patterns that indicate the *summary* of a failed compile (not individual errors).
# We exclude these from the per-error list to avoid duplicates.
_COMPILE_SUMMARY_PATTERNS = [
    re.compile(r"NS auto compile failed:\s*\d+\s*Errors?", re.I),
    re.compile(r"^\s*\d+\s*Errors?,\s*\d+\s*Warnings?\s*$", re.I | re.M),
]


def check_compile_result(strategy_name=None, since=None, timeout=30, poll_interval=1.0):
    """Wait for and parse the most recent compile result from the NT8 log.

    Args:
        strategy_name: optional — only consider compile events mentioning this name
        since: datetime — only consider log entries after this timestamp (default: now-60s)
        timeout: how long to wait for a fresh compile entry (seconds)
        poll_interval: how often to re-read the log file

    Returns:
        dict with keys:
            success: bool
            errors: list[dict] with keys {message, line} (empty on success)
            raw_excerpt: str — last ~50 lines of log (for debugging)
            log_file: str — path to the log file that was read
    """
    if since is None:
        since = datetime.now()

    log_file = get_today_log_file()
    deadline = time.time() + timeout

    while time.time() < deadline:
        if log_file and os.path.exists(log_file):
            text = _read_tail(log_file)
            # Find the most recent "compile" mention
            compile_blocks = list(re.finditer(
                r"(NS auto compile|Compilation|Code::Compile|Build)",
                text,
                re.I,
            ))
            if compile_blocks:
                last = compile_blocks[-1]
                # Take a window around the last compile mention
                start = max(0, last.start() - 200)
                end = min(len(text), last.start() + 4000)
                block = text[start:end]

                # If a strategy name was given, make sure this block mentions it
                # (NT8 logs the strategy name when it's the file being compiled)
                if strategy_name and strategy_name.lower() not in block.lower():
                    # Could be a different compile — keep polling
                    time.sleep(poll_interval)
                    continue

                # Check for errors first (more specific than success)
                errors = _extract_errors(block)
                if errors:
                    return {
                        "success": False,
                        "errors": errors,
                        "raw_excerpt": block[-1500:],
                        "log_file": log_file,
                    }

                # Then check for explicit success markers
                for pat in _COMPILE_SUCCESS_PATTERNS:
                    if pat.search(block):
                        return {
                            "success": True,
                            "errors": [],
                            "raw_excerpt": block[-1500:],
                            "log_file": log_file,
                        }
        time.sleep(poll_interval)

    # Timeout — we couldn't find a definitive compile entry
    return {
        "success": None,  # unknown — caller should treat as soft-fail
        "errors": [],
        "raw_excerpt": _read_tail(log_file)[-1500:] if log_file else "",
        "log_file": log_file,
        "timeout": True,
    }


def _extract_errors(block):
    """Extract structured error info from a log block."""
    errors = []
    seen = set()

    # First, identify summary lines so we can exclude them from the per-error list.
    summary_spans = []
    for pat in _COMPILE_SUMMARY_PATTERNS:
        for m in pat.finditer(block):
            summary_spans.append((m.start(), m.end()))

    def is_in_summary(span_start):
        return any(s <= span_start < e for s, e in summary_spans)

    for pat in _COMPILE_ERROR_PATTERNS:
        for m in pat.finditer(block):
            if is_in_summary(m.start()):
                continue

            groups = m.groups()
            # Patterns are heterogeneous: some are (line, msg), others (msg, line?), others (msg,).
            # Figure out which group is the line number and which is the message.
            line = None
            msg = None
            for g in groups:
                if g is None:
                    continue
                # If the group is purely digits, treat it as the line number
                if g.strip().isdigit() and line is None:
                    try:
                        line = int(g.strip())
                        continue
                    except ValueError:
                        pass
                # Otherwise, treat it as the message
                if msg is None:
                    msg = g.strip()
            if not msg:
                msg = m.group(0).strip()
            if not msg:
                continue

            # Also try to find a line number elsewhere in the matched text
            if line is None:
                ln_match = re.search(r"line\s*(\d+)", m.group(0), re.I)
                if ln_match:
                    try:
                        line = int(ln_match.group(1))
                    except ValueError:
                        pass

            key = (msg[:80], line)
            if key in seen:
                continue
            seen.add(key)
            errors.append({"message": msg[:500], "line": line})

    return errors


def format_errors_for_agent(result):
    """Format compile errors as a single human/AI-readable string.

    Example output:
        COMPILE FAILED — 2 error(s):
          Line 42: 'SMA' does not contain a definition for 'Periiod'
          Line 87: Cannot implicitly convert type 'int' to 'bool'
    """
    if result.get("success") is True:
        return "COMPILE OK"
    if result.get("success") is None:
        return (
            "COMPILE STATUS UNKNOWN — NT8 did not log a definitive success/failure "
            "within the timeout. Last log excerpt:\n"
            + (result.get("raw_excerpt") or "(empty)")
        )
    errors = result.get("errors", [])
    if not errors:
        return (
            "COMPILE FAILED — NT8 logged a failure but no structured errors "
            "could be extracted. Raw log excerpt:\n"
            + (result.get("raw_excerpt") or "(empty)")
        )
    out = [f"COMPILE FAILED — {len(errors)} error(s):"]
    for e in errors:
        ln = f"Line {e['line']}: " if e.get("line") else ""
        out.append(f"  {ln}{e['message']}")
    return "\n".join(out)


if __name__ == "__main__":
    # Self-test: scan today's log for the most recent compile event
    result = check_compile_result(timeout=2)
    print(format_errors_for_agent(result))
    print("\n--- raw ---")
    print(result.get("raw_excerpt", "(none)"))
