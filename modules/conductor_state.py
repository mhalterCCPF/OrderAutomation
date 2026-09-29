"""Thread-safe in-memory Conductor state persisted as one JSON snapshot."""

from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from modules.config import ROOT_DIR

STATE_PATH = ROOT_DIR / "conductor_state.json"
STATE_VERSION = 1


class ConductorState:
    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = Path(path)
        self.lock = threading.Lock()
        self.queued_orders: dict[str, dict[str, Any]] = {}
        self.active_assignments: dict[str, dict[str, Any]] = {}
        self.completed_orders: dict[str, dict[str, Any]] = {}
        self.registered_loaders: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        if not isinstance(saved, dict) or saved.get("version") != STATE_VERSION:
            raise ValueError("Unsupported Conductor state file format.")
        for name in self._state_names():
            value = saved.get(name, {})
            if not isinstance(value, dict):
                raise ValueError(f"Conductor state field {name!r} must be an object.")
            setattr(self, name, value)

    @staticmethod
    def _state_names() -> tuple[str, ...]:
        return ("queued_orders", "active_assignments", "completed_orders", "registered_loaders")

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        state = {"version": STATE_VERSION}
        state.update({name: getattr(self, name) for name in self._state_names()})
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(temporary_path, self.path)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return deepcopy({name: getattr(self, name) for name in self._state_names()})

    def replace_queued_orders(self, orders: list[dict[str, Any]]) -> None:
        refreshed = {}
        for order in orders:
            order_id = str(order["id"])
            tags = {str(tag).lower() for tag in order.get("tags", [])}
            if "processing" in tags or "files_ready" in tags:
                continue
            refreshed[order_id] = order
        with self.lock:
            active_ids = set(self.active_assignments)
            completed_ids = set(self.completed_orders)
            self.queued_orders = {
                order_id: order for order_id, order in refreshed.items()
                if order_id not in active_ids and order_id not in completed_ids
            }
            self._save_locked()

    def reconcile_completed_orders(self, unfulfilled_ids: set[str]) -> list[dict[str, Any]]:
        with self.lock:
            for order_id in list(self.completed_orders):
                if order_id not in unfulfilled_ids:
                    del self.completed_orders[order_id]
            pending = deepcopy(list(self.completed_orders.values()))
            self._save_locked()
            return pending

    def register_loader(self, loader_id: str, details: dict[str, Any]) -> None:
        with self.lock:
            existing = self.registered_loaders.get(loader_id, {})
            self.registered_loaders[loader_id] = {**existing, **details, "loader_id": loader_id}
            self._save_locked()

    def update_loader(self, loader_id: str, details: dict[str, Any]) -> None:
        with self.lock:
            if loader_id not in self.registered_loaders:
                raise KeyError(f"Loader {loader_id!r} is not registered.")
            self.registered_loaders[loader_id].update(details)
            self._save_locked()

    def mark_stale_loaders(self, heartbeat_timeout: int) -> int:
        cutoff = time.time() - heartbeat_timeout
        changed = 0
        with self.lock:
            for loader_id, loader in self.registered_loaders.items():
                last_seen = loader.get("last_seen")
                if not isinstance(last_seen, (int, float)) or last_seen >= cutoff:
                    continue
                if loader.get("status") in ("unavailable", "interrupted"):
                    continue
                assignment = next(
                    (item for item in self.active_assignments.values()
                     if item.get("loader_id") == loader_id),
                    None,
                )
                if assignment:
                    if (
                        assignment.get("status") == "processing"
                        and not assignment.get("prepared")
                        and time.time() - assignment.get("assigned_at", last_seen) < max(120, heartbeat_timeout * 3)
                    ):
                        continue
                    assignment["status"] = "interrupted"
                    assignment["error"] = "Loader heartbeat timed out."
                    loader.update({"status": "interrupted", "error": assignment["error"]})
                else:
                    loader.update({"status": "unavailable", "order_id": None})
                changed += 1
            if changed:
                self._save_locked()
        return changed

    def atomic_assign_order(self, loader_id: str) -> dict[str, Any] | None:
        with self.lock:
            loader = self.registered_loaders.get(loader_id)
            if loader is None or loader.get("status") != "ready":
                return None
            if any(item.get("loader_id") == loader_id for item in self.active_assignments.values()):
                return None
            for order_id, order in list(self.queued_orders.items()):
                assignment = {
                    "order_id": order_id,
                    "loader_id": loader_id,
                    "status": "processing",
                    "assigned_at": time.time(),
                    "order": order,
                    "completed_units": [],
                }
                del self.queued_orders[order_id]
                self.active_assignments[order_id] = assignment
                loader.update({"status": "busy", "order_id": order_id})
                self._save_locked()
                return deepcopy(assignment)
            return None

    def rollback_assignment(self, order_id: str, reason: str) -> None:
        with self.lock:
            assignment = self.active_assignments.pop(order_id, None)
            if assignment is None:
                return
            order = assignment["order"]
            order_tags = {str(tag).lower() for tag in order.get("tags", [])}
            order_tags.discard("processing")
            order["tags"] = sorted(order_tags)
            self.queued_orders[order_id] = order
            loader = self.registered_loaders.get(assignment["loader_id"])
            if loader:
                loader.update({"status": "interrupted", "order_id": order_id, "error": reason})
            self._save_locked()

    def set_assignment_tags(self, order_id: str, tags: list[str]) -> None:
        with self.lock:
            assignment = self.active_assignments[order_id]
            assignment["order"]["tags"] = list(tags)
            self._save_locked()

    def assignment_for_loader(self, loader_id: str) -> dict[str, Any] | None:
        with self.lock:
            assignment = next(
                (item for item in self.active_assignments.values() if item["loader_id"] == loader_id),
                None,
            )
            return deepcopy(assignment) if assignment else None

    def cache_assignment(self, order_id: str, prepared: dict[str, Any], bundle_path: str) -> None:
        with self.lock:
            assignment = self.active_assignments[order_id]
            assignment["prepared"] = prepared
            assignment["bundle_path"] = bundle_path
            self._save_locked()

    def acknowledge_print_unit(self, loader_id: str, order_id: str, unit_index: int) -> None:
        with self.lock:
            assignment = self.active_assignments.get(order_id)
            if assignment is None or assignment.get("loader_id") != loader_id:
                raise ValueError("The order is not assigned to this loader.")
            if assignment.get("status") != "processing":
                raise ValueError("The assignment is not in a printable state.")
            units = (assignment.get("prepared") or {}).get("print_units", [])
            valid_indexes = {unit.get("index") for unit in units}
            if unit_index not in valid_indexes:
                raise ValueError("Print unit index is out of range.")
            completed = set(assignment.get("completed_units", []))
            completed.add(unit_index)
            assignment["completed_units"] = sorted(completed)
            self._save_locked()

    def complete_assignment(
        self,
        order_id: str,
        loader_status: str,
        loader_id: str | None = None,
    ) -> dict[str, Any]:
        if loader_status not in ("ready", "unavailable"):
            raise ValueError("Completed loader status must be ready or unavailable.")
        with self.lock:
            assignment = self.active_assignments.get(order_id)
            if assignment is None:
                completed = self.completed_orders.get(order_id)
                if completed and (loader_id is None or completed.get("loader_id") == loader_id):
                    return deepcopy(completed)
                raise ValueError("No active assignment exists for this order.")
            if loader_id is not None and assignment.get("loader_id") != loader_id:
                raise ValueError("The order is assigned to a different loader.")
            required = {
                unit.get("index")
                for unit in (assignment.get("prepared") or {}).get("print_units", [])
            }
            if set(assignment.get("completed_units", [])) != required:
                raise ValueError("Cannot complete an assignment until every print unit is acknowledged.")
            assignment = self.active_assignments.pop(order_id)
            completed = {
                "order_id": order_id,
                "order": assignment["order"],
                "loader_id": assignment["loader_id"],
                "status": "awaiting_shopify",
            }
            self.completed_orders[order_id] = completed
            loader = self.registered_loaders[assignment["loader_id"]]
            loader.update({"status": loader_status, "order_id": None})
            self._save_locked()
            return deepcopy(completed)

    def requeue_interrupted(self, order_id: str) -> None:
        with self.lock:
            assignment = self.active_assignments.get(order_id)
            if assignment is None or assignment.get("status") != "interrupted":
                raise ValueError("Only interrupted assignments can be requeued.")
            assignment = self.active_assignments.pop(order_id)
            order = assignment["order"]
            order["tags"] = [tag for tag in order.get("tags", []) if str(tag).lower() != "processing"]
            self.queued_orders[order_id] = order
            loader = self.registered_loaders.get(assignment["loader_id"])
            if loader:
                loader.update({"status": "unavailable", "order_id": None, "error": None})
            self._save_locked()