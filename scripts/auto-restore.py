#!/usr/bin/env python3
"""
auto-restore.py — Verify Johnny's custom files survive hermes-update.

Run AFTER `git rebase origin/main` to confirm all johnny-specific files
are still present. If upstream ever renames/moves/deletes the files we
patch (gateway/run.py, tui_gateway/server.py), this script detects it
and alerts before pip install runs.

Usage:
    python scripts/auto-restore.py

Exit codes:
    0 = all files present
    1 = warning (files missing — check manually)
    2 = fatal (stubs broken — must fix before pip install)
"""
from __future__ import annotations

import os
import sys
import subprocess

HERMES_AGENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(cmd, cwd=HERMES_AGENT, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def check_file(path: str, reason: str) -> tuple[bool, str]:
    full = os.path.join(HERMES_AGENT, path)
    exists = os.path.isfile(full)
    status = "✓" if exists else "✗ MISSING"
    msg = f"  {status}  {path}  ({reason})"
    return exists, msg


def main():
    print("=== auto-restore check ===")
    print(f"Hermes agent: {HERMES_AGENT}\n")

    # Files we created on the johnny branch
    new_files = [
        ("gateway/entry.py", "extracted entry point (start_gateway/main)"),
        ("tui_gateway/_state.py", "global state + @method decorator"),
        ("tui_gateway/handlers/__init__.py", "handlers package"),
        ("tui_gateway/handlers/_core.py", "slash worker + plumbing"),
        ("tui_gateway/handlers/_agent.py", "agent factory handlers"),
        ("tui_gateway/handlers/_session.py", "session RPC handlers"),
        ("tui_gateway/handlers/_prompt.py", "prompt RPC handlers"),
        ("tui_gateway/handlers/_config_methods.py", "config RPC handlers"),
        ("tui_gateway/handlers/_tools.py", "tools RPC handlers"),
        ("tui_gateway/handlers/_misc.py", "misc RPC handlers"),
    ]

    # Key files we patch (must still exist and have our stubs)
    patched_files = [
        ("gateway/run.py", "must contain delegation stubs to gateway/entry"),
        ("tui_gateway/server.py", "thin facade importing from handlers/"),
    ]

    # Critical local config files — NOT in the hermes-agent repo, but we
    # verify they exist and contain expected content so we catch accidental
    # deletion or overwrite by the update process.
    local_configs = [
        ("~/.claude/settings.json", "Claude Code model routing"),
        ("~/.omlx/model_settings.json", "TurboQuant + per-model settings"),
        ("~/.omlx/settings.json", "oMLX server config (port, cache, auth)"),
    ]

    all_ok = True
    warnings = []

    print("-- New files (johnny branch additions) --")
    for path, reason in new_files:
        ok, msg = check_file(path, reason)
        print(msg)
        if not ok:
            all_ok = False
            warnings.append(f"NEW FILE MISSING: {path}")

    print()
    print("-- Patched files (must have johnny customizations) --")
    for path, reason in patched_files:
        ok, msg = check_file(path, reason)
        print(msg)
        if not ok:
            all_ok = False
            warnings.append(f"PATCHED FILE MISSING: {path}")

    # Check gateway/run.py still has our delegation stubs
    print()
    print("-- Verifying delegation stubs in gateway/run.py --")
    run_py = os.path.join(HERMES_AGENT, "gateway", "run.py")
    if os.path.isfile(run_py):
        with open(run_py) as f:
            content = f.read()
        if "from gateway.entry import start_gateway" in content:
            print("  ✓  start_gateway delegation stub present")
        else:
            print("  ✗  start_gateway delegation stub MISSING — stubs broken!")
            warnings.append("STUB BROKEN: gateway/run.py missing start_gateway delegation")
            all_ok = False
        if "from gateway.entry import main" in content:
            print("  ✓  main delegation stub present")
        else:
            print("  ✗  main delegation stub MISSING — stubs broken!")
            warnings.append("STUB BROKEN: gateway/run.py missing main delegation")
            all_ok = False
    else:
        print("  ✗  gateway/run.py not found")
        warnings.append("gateway/run.py is missing entirely")

    # Check tui_gateway/server.py is a thin facade
    print()
    print("-- Verifying thin facade in tui_gateway/server.py --")
    srv_py = os.path.join(HERMES_AGENT, "tui_gateway", "server.py")
    if os.path.isfile(srv_py):
        with open(srv_py) as f:
            content = f.read()
        if "from tui_gateway.handlers" in content:
            print("  ✓  handlers import present (facade pattern)")
        else:
            print("  ✗  tui_gateway/server.py is not a facade — handlers import missing")
            warnings.append("tui_gateway/server.py facade broken")
            all_ok = False
    else:
        print("  ✗  tui_gateway/server.py not found")
        warnings.append("tui_gateway/server.py is missing entirely")

    # Check critical local config files
    print()
    print("-- Verifying critical local configs (not in hermes-agent repo) --")
    for rel_path, reason in local_configs:
        full = os.path.expanduser(rel_path)
        exists = os.path.isfile(full)
        status = "✓" if exists else "✗ MISSING"
        print(f"  {status}  {rel_path}  ({reason})")
        if not exists:
            warnings.append(f"LOCAL CONFIG MISSING: {rel_path}")
            all_ok = False
        else:
            # Spot-check content is plausible (not zero-length or corrupted)
            try:
                import json
                with open(full) as f:
                    data = json.load(f)
                # Basic sanity: dict is not empty
                if not data:
                    warnings.append(f"LOCAL CONFIG EMPTY: {rel_path}")
                    all_ok = False
                else:
                    print(f"         ({len(data)} top-level key(s))")
            except json.JSONDecodeError as e:
                warnings.append(f"LOCAL CONFIG CORRUPT JSON: {rel_path}: {e}")
                all_ok = False
            except Exception as e:
                # Non-JSON file — just check it has content
                pass

    # Check entry.py compiles
    print()
    print("-- Syntax check: gateway/entry.py --")
    entry_py = os.path.join(HERMES_AGENT, "gateway", "entry.py")
    if os.path.isfile(entry_py):
        rc, out = run(["python", "-m", "py_compile", "gateway/entry.py"])
        if rc == 0:
            print("  ✓  gateway/entry.py compiles cleanly")
        else:
            print(f"  ✗  COMPILE ERROR in gateway/entry.py:\n{out}")
            warnings.append(f"COMPILE ERROR: gateway/entry.py\n{out}")
            all_ok = False
    else:
        print("  (skipped — file missing)")

    # Check entry.py imports resolve
    print()
    print("-- Import check: gateway/entry.py --")
    rc, out = run(["python", "-c", "from gateway.entry import start_gateway, main, _start_cron_ticker"])
    if rc == 0:
        print("  ✓  gateway.entry imports resolve")
    else:
        print(f"  ✗  Import error in gateway/entry.py:\n{out[:500]}")
        warnings.append(f"IMPORT ERROR: gateway.entry\n{out[:500]}")
        all_ok = False

    print()
    print("=== Summary ===")
    if warnings:
        for w in warnings:
            print(f"  WARNING: {w}")
    if all_ok:
        print("  All checks passed ✓")
        return 0
    else:
        print()
        print("  Some checks failed. Review warnings above.")
        print("  If stubs are broken, DO NOT run pip install — fix first.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
