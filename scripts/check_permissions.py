#!/usr/bin/env python3
"""Report whether the independent helper can reach the macOS services it needs."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import apple_scheduler as scheduler  # type: ignore


def check(name: str, script: str) -> dict:
    try:
        output = scheduler.run_via_helper(script, timeout=20)
        return {"name": name, "ok": True, "output": output}
    except Exception as exc:
        return {"name": name, "ok": False, "error": str(exc)}


def main() -> int:
    app = scheduler.helper_app_path()
    result = {
        "helper_app": str(app) if app else None,
        "helper_installed": bool(app),
        "checks": [],
    }
    if app:
        result["checks"].append(check("reminders", 'tell application "Reminders" to get name of lists'))
        result["checks"].append(check("calendar", 'tell application "Calendar" to get name of calendars'))
        accessibility = check("accessibility_system_events", 'tell application "System Events" to get UI elements enabled')
        if accessibility.get("ok") and str(accessibility.get("output", "")).strip().lower() != "true":
            accessibility["ok"] = False
            accessibility["error"] = "Accessibility permission is not enabled for Apple Chat Scheduler Helper."
        result["checks"].append(accessibility)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if app and all(item["ok"] for item in result["checks"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
