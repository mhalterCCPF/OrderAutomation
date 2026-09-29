"""Machine-readable headless interface for eufyLoaderRobot integration."""

from __future__ import annotations

import json
import sys
from typing import Any

from modules.config import DEFAULT_CONFIG, load_config, load_secrets
from modules.order_workflow import WorkflowOrchestrator

PROTOCOL_VERSION = 1
RUNTIME_CONFIG_KEYS = set(DEFAULT_CONFIG)


def execute_request(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("version") != PROTOCOL_VERSION:
        raise ValueError(f"Unsupported protocol version: {request.get('version')!r}")

    overrides = request.get("config", {})
    if not isinstance(overrides, dict):
        raise ValueError("The config field must be a JSON object.")
    unknown_keys = set(overrides) - RUNTIME_CONFIG_KEYS
    if unknown_keys:
        raise ValueError(f"Unsupported runtime config keys: {', '.join(sorted(unknown_keys))}")

    saved_config = load_config()
    config = {**saved_config, **overrides, **load_secrets()}
    if "company" in overrides:
        if not isinstance(overrides["company"], dict):
            raise ValueError("The company config field must be a JSON object.")
        config["company"] = {**saved_config.get("company", {}), **overrides["company"]}
    workflow = WorkflowOrchestrator(config)
    action = request.get("action")

    if action == "prepare_next_order":
        return workflow.prepare_next_order()
    if action == "stage_print_unit":
        return workflow.stage_print_unit(request.get("token"), request.get("unit_index"))
    if action == "complete_print_unit":
        return workflow.complete_print_unit(request.get("token"), request.get("unit_index"))
    if action == "complete_order":
        return workflow.complete_order(request.get("token"))
    raise ValueError(f"Unsupported action: {action!r}")


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("The request must be a JSON object.")
        response = {"version": PROTOCOL_VERSION, **execute_request(request)}
        exit_code = 0
    except Exception as error:
        response = {
            "version": PROTOCOL_VERSION,
            "status": "error",
            "error": {"type": type(error).__name__, "message": str(error)},
        }
        exit_code = 1

    json.dump(response, sys.stdout)
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())