"""
NT8 AutomationId Discovery Tool
================================
Walks the UIA tree of NinjaTrader windows and prints every element's
AutomationId, Name, and ControlType. Use this to discover the correct
IDs after NT8 updates break the backtester.

Usage:
  python nt8_discover.py --window "NinjaScript Editor"
  python nt8_discover.py --window "Strategy Analyzer"
  python nt8_discover.py --all
"""
import sys
import time
import argparse

try:
    from pywinauto import Desktop
except ImportError:
    print("ERROR: pywinauto not installed. Run: pip install pywinauto")
    sys.exit(1)


def discover_window(window_title, depth=0, max_depth=5):
    """Walk the UIA tree of a window and print element info."""
    desktop = Desktop(backend="uia")
    
    # Find the window
    windows = desktop.windows()
    target = None
    for w in windows:
        title = w.window_text() or ""
        if window_title.lower() in title.lower():
            target = w
            break
    
    if not target:
        print(f"Window '{window_title}' not found. Available windows:")
        for w in windows:
            t = w.window_text()
            if t:
                print(f"  - {t}")
        return
    
    print(f"\n{'='*60}")
    print(f"Discovering: {target.window_text()}")
    print(f"  AutoId: {target.automation_id()}")
    print(f"{'='*60}")
    
    # Use pywinauto's built-in control identifier printer
    try:
        target.print_control_identifiers(depth=max_depth)
    except Exception as e:
        print(f"  [WARN] print_control_identifiers failed: {e}")
        print("  [INFO] Falling back to manual tree walk...")
        walk_tree(target, depth=depth, max_depth=max_depth)


def walk_tree(control, depth=0, max_depth=5):
    """Recursively walk the control tree."""
    if depth > max_depth:
        return
    
    indent = "  " * depth
    
    try:
        auto_id = control.automation_id() or ""
        name = control.window_text() or ""
        ctrl_type = control.control_type() or ""
        
        # Only print elements that have some identifying info
        if auto_id or name:
            parts = []
            if auto_id:
                parts.append(f"id={auto_id}")
            if name:
                parts.append(f"name={name[:50]}")
            if ctrl_type:
                parts.append(f"type={ctrl_type}")
            print(f"{indent}{' | '.join(parts)}")
    except Exception:
        pass
    
    try:
        for child in control.children():
            walk_tree(child, depth + 1, max_depth)
    except Exception:
        pass


def discover_all():
    """Discover all top-level windows."""
    desktop = Desktop(backend="uia")
    windows = desktop.windows()
    
    print(f"\n{'='*60}")
    print(f"All NinjaTrader Windows")
    print(f"{'='*60}")
    
    for w in windows:
        title = w.window_text()
        if title:
            auto_id = w.automation_id() or ""
            print(f"  {title}  (id={auto_id})")
    
    print(f"\n--- Detailed trees ---\n")
    for w in windows:
        title = w.window_text()
        if title and ("NinjaScript" in title or "Strategy" in title or "NinjaTrader" in title):
            discover_window(title, max_depth=3)


def main():
    parser = argparse.ArgumentParser(description="NT8 UIAutomationId Discovery Tool")
    parser.add_argument("--window", "-w", help="Window title to discover")
    parser.add_argument("--all", "-a", action="store_true", help="Discover all windows")
    parser.add_argument("--depth", "-d", type=int, default=4, help="Max tree depth")
    
    args = parser.parse_args()
    
    if args.all:
        discover_all()
    elif args.window:
        discover_window(args.window, max_depth=args.depth)
    else:
        # Default: discover Strategy Analyzer
        discover_window("Strategy Analyzer", max_depth=args.depth)


if __name__ == "__main__":
    main()
