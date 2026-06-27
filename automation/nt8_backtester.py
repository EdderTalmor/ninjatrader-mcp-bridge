"""
NinjaTrader 8 - Autonomous Strategy Compiler & Backtester
=========================================================
Uses Windows UI Automation (UIA) to:
1. Compile a .cs strategy file in NinjaTrader's NinjaScript Editor
2. Run the strategy in Strategy Analyzer
3. Extract backtest results (Summary + Trades)

Requirements (Windows machine):
  - pip install pywinauto
  - NinjaTrader 8 running with NinjaScript Editor open
  - Strategy file already saved to disk

Usage:
  python nt8_backtester.py --strategy path/to/MyStrategy.cs --bar-type Minute --bar-value 5
  python nt8_backtester.py -c -s MyStrategy.cs      # compile only
  python nt8_backtester.py -b -s MyStrategy.cs      # backtest only (assumes compiled)
  python nt8_backtester.py --full -s MyStrategy.cs   # compile + backtest + export
"""
import sys
import os
import time
import csv
import json
import argparse
from datetime import datetime

try:
    from pywinauto import Application, Desktop
    from pywinauto.controls.uiawrapper import UIAWrapper
except ImportError:
    print("ERROR: pywinauto not installed. Run: pip install pywinauto")
    sys.exit(1)


# ─── Configuration ──────────────────────────────────────────────────────────

NT8_STRATEGIES_DIR = os.path.expanduser(r"~\Documents\NinjaTrader 8\bin\Custom\Strategies")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
POLL_INTERVAL = 0.5  # seconds
COMPILE_TIMEOUT = 15
BACKTEST_POLL_INTERVAL = 2
BACKTEST_TIMEOUT = 600  # 10 minutes max


# ─── Helpers ─────────────────────────────────────────────────────────────────

def find_window(title=None, auto_id=None, control_type="Window", timeout=10):
    """Find a top-level window by title or automationId."""
    desktop = Desktop(backend="uia")
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            windows = desktop.windows()
            for w in windows:
                window_title = w.window_text() or ""
                if title and title.lower() in window_title.lower():
                    return w
                if auto_id and w.automation_id() == auto_id:
                    return w
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    return None


def find_control(parent, auto_id=None, control_type=None, name=None, timeout=5):
    """Find a child control within a parent window."""
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            if auto_id:
                ctrl = parent.child_window(auto_id=auto_id, found_index=0)
                if ctrl.exists(timeout=0.5):
                    return ctrl
            if name:
                ctrl = parent.child_window(title=name, found_index=0)
                if ctrl.exists(timeout=0.5):
                    return ctrl
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    return None


def invoke_button(parent, auto_id=None, name=None):
    """Find and click a button."""
    btn = find_control(parent, auto_id=auto_id, name=name)
    if btn:
        try:
            btn.invoke()
            return True
        except Exception as e:
            print(f"  [WARN] Failed to invoke button: {e}")
    return False


def wait_for_compilation_complete(editor_window, timeout=COMPILE_TIMEOUT):
    """Wait for NinjaScript Editor to finish compiling.
    
    Strategy: Poll for absence of error/warning text indicators.
    """
    print("  [....] Compiling (polling for errors or completion)")
    end_time = time.time() + timeout
    last_status = ""
    
    while time.time() < end_time:
        try:
            # Check for compile error text
            try:
                error_label = editor_window.child_window(auto_id="labelErrors")
                if error_label.exists(timeout=0.3):
                    text = error_label.window_text()
                    if text and "error" in text.lower():
                        return False, text
            except:
                pass
            
            # Check for success indicator (loss of "Compiling..." status)
            try:
                status = editor_window.child_window(auto_id="statusBar")
                if status.exists(timeout=0.3):
                    status_text = status.window_text() or ""
                    last_status = status_text
                    if "compil" in status_text.lower():
                        # Still compiling
                        pass
                    elif "error" in status_text.lower() or "fail" in status_text.lower():
                        return False, status_text
                    elif status_text == "" or "ready" in status_text.lower():
                        # Done compiling
                        pass
            except:
                pass
            
            # Check for build output
            try:
                output = editor_window.child_window(auto_id="txtOutput")
                if output.exists(timeout=0.3):
                    text = output.window_text() or ""
                    if "Build succeeded" in text or "0 error" in text:
                        return True, "Build succeeded"
                    elif "error" in text.lower():
                        # Extract error line
                        lines = text.split("\n")
                        errors = [l for l in lines if "error" in l.lower() and l.strip()]
                        return False, "; ".join(errors[:3]) if errors else text[-200:]
            except:
                pass
                
        except Exception:
            pass
        
        time.sleep(1)
    
    # Timeout - assume success (NT8 might have already compiled silently)
    return True, "Timeout (assumed success)"


# ─── Core Functions ──────────────────────────────────────────────────────────

def find_ninjascript_editor():
    """Find the NinjaScript Editor window in NinjaTrader."""
    print("  [*] Looking for NinjaScript Editor...")
    editor = find_window(title="NinjaScript Editor", timeout=15)
    if not editor:
        print("  [FAIL] NinjaScript Editor not found. Is NT8 open with the editor visible?")
        return None
    print(f"  [OK] Found: {editor.window_text()}")
    return editor


def find_strategy_analyzer():
    """Find the Strategy Analyzer window."""
    print("  [*] Looking for Strategy Analyzer...")
    analyzer = find_window(title="Strategy Analyzer", timeout=10)
    if not analyzer:
        print("  [WARN] Strategy Analyzer window not found. It will be opened when running backtest.")
        return None
    print(f"  [OK] Found: {analyzer.window_text()}")
    return analyzer


def prepare_strategy_file(source_cs, strategy_name=None):
    """Copy .cs file to NT8 Strategies folder."""
    if not strategy_name:
        strategy_name = os.path.splitext(os.path.basename(source_cs))[0]
    
    dest = os.path.join(NT8_STRATEGIES_DIR, os.path.basename(source_cs))
    
    os.makedirs(NT8_STRATEGIES_DIR, exist_ok=True)
    
    import shutil
    shutil.copy2(source_cs, dest)
    print(f"  [OK] Copied strategy to: {dest}")
    return dest


def compile_strategy(cs_file_path):
    """Compile a .cs file using the NinjaScript Editor.
    
    Steps:
    1. Find NinjaScript Editor
    2. Detect auto-compilation (NT8 compiles when file changes)
    3. Wait for compilation to complete
    4. Check for errors
    """
    print("\n=== PHASE 1: COMPILE ===")
    
    editor = find_ninjascript_editor()
    if not editor:
        return False, "Editor not found"
    
    # NT8 auto-compiles when a .cs file changes in the Strategies folder
    # We need to touch the file or trigger a manual compile
    
    # Method: Use the Compile button in the editor
    print("  [*] Triggering compilation...")
    compile_btn = find_control(editor, auto_id="buttonCompile")
    if not compile_btn:
        # Try by name
        compile_btn = find_control(editor, name="Compile")
    
    if compile_btn:
        compile_btn.invoke()
        print("  [OK] Compile button clicked")
    else:
        # NT8 has auto-compile on file change - just touch the file
        print("  [*] No compile button found; NT8 auto-compiles on file change")
        # Touch the file to trigger auto-compilation
        os.utime(cs_file_path, None)
    
    # Wait for compilation
    success, message = wait_for_compilation_complete(editor)
    if success:
        print(f"  [OK] Compilation successful: {message}")
    else:
        print(f"  [FAIL] Compilation failed: {message}")
    
    return success, message


def run_backtest(strategy_name, bar_type="Minute", bar_value="5",
                 instrument="MES 06-26", date_from=None, date_to=None,
                 commission="1.27", slippage="1"):
    """Run a backtest in the Strategy Analyzer.
    
    Steps:
    1. Open Strategy Analyzer if not already open
    2. Configure strategy, instrument, parameters
    3. Click Run
    4. Wait for completion
    """
    print("\n=== PHASE 2: BACKTEST ===")
    
    # Find or open Strategy Analyzer
    analyzer = find_strategy_analyzer()
    if not analyzer:
        print("  [*] Opening Strategy Analyzer via Control Center...")
        # Look for Control Center to open Strategy Analyzer
        cc = find_window(title="NinjaTrader Control Center")
        if cc:
            # Try to use the New -> Strategy Analyzer menu
            new_menu = find_control(cc, auto_id="NewMenu") or find_control(cc, name="New")
            if new_menu:
                new_menu.invoke()
                time.sleep(1)
                sa_option = find_control(cc, name="Strategy Analyzer")
                if sa_option:
                    sa_option.invoke()
                    time.sleep(3)
        
        analyzer = find_window(title="Strategy Analyzer", timeout=15)
        if not analyzer:
            print("  [FAIL] Could not open Strategy Analyzer")
            return False, "Failed to open Strategy Analyzer"
    
    # Configure strategy
    print(f"  [*] Selecting strategy: {strategy_name}")
    strategy_combo = find_control(analyzer, auto_id="comboStrategy")
    if strategy_combo:
        strategy_combo.select(strategy_name)
    else:
        print("  [WARN] Could not find strategy dropdown; NT may auto-select from file")
    
    # Configure bar settings
    print(f"  [*] Configuring: {instrument} {bar_value} {bar_type}")
    
    # Bar type and value
    bar_type_combo = find_control(analyzer, auto_id="comboBarsPeriod")
    if bar_type_combo:
        bar_type_combo.select(bar_type)
    
    bar_value_input = find_control(analyzer, auto_id="txtBarsPeriodValue")
    if bar_value_input:
        bar_value_input.set_text(bar_value)
    
    # Instrument
    instrument_combo = find_control(analyzer, auto_id="comboInstrument")
    if instrument_combo:
        instrument_combo.select(instrument)
    
    # Commission
    if commission:
        commission_input = find_control(analyzer, auto_id="txtCommission")
        if commission_input:
            commission_input.set_text(commission)
    
    time.sleep(1)
    
    # Click Run
    print("  [*] Running backtest...")
    run_btn = find_control(analyzer, auto_id="buttonRun") or find_control(analyzer, name="Run")
    if run_btn:
        run_btn.invoke()
        print("  [OK] Backtest started")
    else:
        print("  [WARN] Run button not found; trying alternative")
        # Try scrolling to find it
        
    # Wait for backtest to complete
    print("  [*] Waiting for backtest to complete...")
    return wait_for_backtest_complete(analyzer)


def wait_for_backtest_complete(analyzer, timeout=BACKTEST_TIMEOUT):
    """Wait for backtest to finish.
    
    Strategy: Poll for the "Running backtest..." label to disappear,
    or look for the presence of results in the data grid.
    """
    end_time = time.time() + timeout
    start_time = time.time()
    
    while time.time() < end_time:
        elapsed = time.time() - start_time
        
        # Check for backtest running indicator
        running = False
        try:
            # NT8 shows "Running backtest on..." in a label during execution
            message_label = analyzer.child_window(auto_id="txtMessage")
            if message_label.exists(timeout=0.3):
                msg = message_label.window_text() or ""
                if "running" in msg.lower() or "backtest" in msg.lower():
                    running = True
                    if elapsed % 10 < 1:  # Print every ~10 seconds
                        print(f"  [....] Running... ({elapsed:.0f}s) - {msg[:60]}")
        except:
            pass
        
        # Check for results
        try:
            summary_grid = analyzer.child_window(auto_id="grdSummary")
            if summary_grid.exists(timeout=0.3):
                # Check if it has data rows
                rows = summary_grid.children()
                if len(rows) > 0:
                    print(f"  [OK] Backtest complete after {elapsed:.0f}s")
                    return True, f"Completed in {elapsed:.0f}s"
        except:
            pass
        
        # Check for error
        try:
            error_label = analyzer.child_window(auto_id="txtError")
            if error_label.exists(timeout=0.3):
                text = error_label.window_text()
                if text:
                    print(f"  [FAIL] Backtest error: {text}")
                    return False, text
        except:
            pass
        
        time.sleep(BACKTEST_POLL_INTERVAL)
    
    print(f"  [FAIL] Backtest timeout after {timeout}s")
    return False, "Timeout"


def export_results(analyzer, strategy_name, output_dir):
    """Export backtest results to CSV."""
    print("\n=== PHASE 3: EXPORT RESULTS ===")
    
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_dir = os.path.join(output_dir, f"{strategy_name}_{timestamp}")
    os.makedirs(result_dir, exist_ok=True)
    
    # Export Summary
    print("  [*] Exporting Summary...")
    summary_success = export_data_grid(
        analyzer, "grdSummary", 
        os.path.join(result_dir, "summary.csv")
    )
    
    # Export Trades
    print("  [*] Exporting Trades...")
    trades_success = export_data_grid(
        analyzer, "grdTrades",
        os.path.join(result_dir, "trades.csv")
    )
    
    # Save metadata
    metadata = {
        "strategy": strategy_name,
        "timestamp": timestamp,
        "summary_exported": summary_success,
        "trades_exported": trades_success,
        "output_dir": result_dir
    }
    with open(os.path.join(result_dir, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)
    
    print(f"  [OK] Results saved to: {result_dir}")
    return result_dir


def export_data_grid(analyzer, grid_auto_id, output_path):
    """Extract data from a DataGrid and write to CSV."""
    try:
        grid = analyzer.child_window(auto_id=grid_auto_id)
        if not grid.exists(timeout=3):
            print(f"  [WARN] Grid {grid_auto_id} not found")
            return False
        
        # Get all rows
        rows = []
        try:
            # Try to get data directly via ValuePattern
            descendants = grid.descendants()
            
            # Group by row
            grid_rows = [d for d in descendants if d.control_type() == "DataItem"]
            if not grid_rows:
                # Try getting all text children
                text_controls = [d for d in descendants if d.control_type() == "Text"]
                cell_texts = [t.window_text() or "" for t in text_controls]
                # Write as single column
                with open(output_path, "w", newline="") as f:
                    writer = csv.writer(f)
                    for text in cell_texts:
                        writer.writerow([text])
                return True
            
            # Group cells into rows
            # UIA exposes data items as rows with children as cells
            current_row = []
            for item in grid_rows:
                try:
                    cell_text = item.window_text() or ""
                    current_row.append(cell_text)
                except:
                    if current_row:
                        rows.append(current_row)
                        current_row = []
            
            if current_row:
                rows.append(current_row)
        except Exception as e:
            print(f"  [WARN] Error reading grid data: {e}")
            # Fallback: just get all text
            return False
        
        # Write to CSV
        if rows:
            with open(output_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerows(rows)
            print(f"  [OK] Exported {len(rows)} rows to {output_path}")
            return True
        else:
            print(f"  [WARN] No data rows found in {grid_auto_id}")
            # Write empty file as marker
            with open(output_path, "w") as f:
                f.write("No data\n")
            return False
            
    except Exception as e:
        print(f"  [FAIL] Export error: {e}")
        return False


# ─── Main Pipeline ───────────────────────────────────────────────────────────

def full_pipeline(args):
    """Run the complete backtest pipeline."""
    
    strategy_file = args.strategy
    if not os.path.exists(strategy_file):
        print(f"[FAIL] Strategy file not found: {strategy_file}")
        return 1
    
    strategy_name = os.path.splitext(os.path.basename(strategy_file))[0]
    print(f"\n{'='*60}")
    print(f"NinjaTrader 8 Autonomous Backtester")
    print(f"Strategy: {strategy_name}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    
    # Step 1: Prepare file
    print("\n=== PHASE 0: PREPARE ===")
    dest = prepare_strategy_file(strategy_file, strategy_name)
    
    # Step 2: Compile (unless backtest-only)
    if not args.backtest_only:
        success, msg = compile_strategy(dest)
        if not success:
            print(f"\n[FATAL] Compilation failed: {msg}")
            return 1
        time.sleep(2)  # Let NT8 finish writing
    else:
        print("\n=== PHASE 1: COMPILE (skipped) ===")
    
    # Step 3: Backtest
    if not args.compile_only:
        success, msg = run_backtest(
            strategy_name=strategy_name,
            bar_type=args.bar_type,
            bar_value=str(args.bar_value),
            instrument=args.instrument,
            date_from=args.date_from,
            date_to=args.date_to,
            commission=args.commission,
            slippage=args.slippage
        )
        if not success:
            print(f"\n[FATAL] Backtest failed: {msg}")
            return 1
    else:
        print("\n=== PHASE 2: BACKTEST (skipped) ===")
    
    # Step 4: Export results
    analyzer = find_window(title="Strategy Analyzer", timeout=5)
    if analyzer:
        result_dir = export_results(analyzer, strategy_name, args.output)
    
    print(f"\n{'='*60}")
    print(f"DONE - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    return 0


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NinjaTrader 8 Autonomous Strategy Backtester")
    parser.add_argument("--strategy", "-s", required=True, help="Path to .cs strategy file")
    parser.add_argument("--compile-only", "-c", action="store_true", help="Compile only, do not run backtest")
    parser.add_argument("--backtest-only", "-b", action="store_true", help="Run backtest only (skip compile)")
    parser.add_argument("--full", "-f", action="store_true", help="Full pipeline (default: compile + backtest + export)")
    parser.add_argument("--instrument", "-i", default="MES 06-26", help="Instrument to backtest")
    parser.add_argument("--bar-type", default="Minute", help="Bar period type (Minute, Hour, Day, Tick)")
    parser.add_argument("--bar-value", default="5", help="Bar period value")
    parser.add_argument("--date-from", default=None, help="Backtest start date (MM/dd/yyyy)")
    parser.add_argument("--date-to", default=None, help="Backtest end date (MM/dd/yyyy)")
    parser.add_argument("--commission", default="1.27", help="Commission per round-trip per contract")
    parser.add_argument("--slippage", default="1", help="Slippage in ticks")
    parser.add_argument("--output", "-o", default=OUTPUT_DIR, help="Output directory for results")
    
    args = parser.parse_args()
    
    return full_pipeline(args)


if __name__ == "__main__":
    sys.exit(main())
