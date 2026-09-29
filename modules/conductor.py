"""Polling, assignment, and completion coordination for the Conductor."""

from __future__ import annotations

import io
import json
import hashlib
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from modules.conductor_state import ConductorState
from modules.config import ROOT_DIR


class ConductorService:
    def __init__(self, workflow, state: ConductorState, config: dict[str, Any]):
        self.workflow = workflow
        self.shopify = workflow.shopify
        self.state = state
        self.config = config
        self.bundle_dir = Path(config.get("conductor_bundle_dir", ROOT_DIR / "conductor_bundles"))
        self.stop_event = threading.Event()
        self.polling_paused = threading.Event()
        self.assignment_lock = threading.Lock()
        self.poll_thread: threading.Thread | None = None
        self.maintenance_thread: threading.Thread | None = None
        self.last_poll_error: str | None = None

    def poll_once(self) -> dict[str, int]:
        orders = self.shopify.get_unfulfilled_orders()
        unfulfilled_ids = {str(order["id"]) for order in orders}
        completed_to_update = self.state.reconcile_completed_orders(unfulfilled_ids)

        updated_count = 0
        for completed in completed_to_update:
            order = completed["order"]
            if self.shopify.start_fulfillment_processing(
                completed["order_id"], order.get("open_fulfillment_order_ids")
            ):
                updated_count += 1
                tags = [tag for tag in order.get("tags", []) if str(tag).lower() != "processing"]
                if tags != order.get("tags", []):
                    self.shopify.update_order_tags(completed["order_id"], tags)

        self.state.replace_queued_orders(orders)
        return {"orders_seen": len(orders), "completion_updates": updated_count}

    def start(self) -> None:
        if self.poll_thread and self.poll_thread.is_alive():
            return
        self.stop_event.clear()
        self.poll_thread = threading.Thread(target=self._poll_loop, name="conductor-poll", daemon=True)
        self.maintenance_thread = threading.Thread(
            target=self._maintenance_loop, name="conductor-maintenance", daemon=True
        )
        self.poll_thread.start()
        self.maintenance_thread.start()

    def pause_polling(self) -> None:
        self.polling_paused.set()

    def resume_polling(self) -> None:
        self.polling_paused.clear()

    def stop(self) -> None:
        self.stop_event.set()
        if self.poll_thread and self.poll_thread.is_alive():
            self.poll_thread.join(timeout=5)
        if self.maintenance_thread and self.maintenance_thread.is_alive():
            self.maintenance_thread.join(timeout=5)

    def _poll_loop(self) -> None:
        interval = max(1, int(self.config.get("orders_update_interval", 15))) * 60
        while not self.stop_event.is_set():
            if not self.polling_paused.is_set():
                try:
                    self.poll_once()
                    self.last_poll_error = None
                except Exception as error:
                    self.last_poll_error = f"{type(error).__name__}: {error}"
            self.stop_event.wait(interval)

    def _maintenance_loop(self) -> None:
        timeout = max(3, int(self.config.get("loader_heartbeat_timeout", 30)))
        interval = min(5, max(1, timeout // 3))
        while not self.stop_event.wait(interval):
            self.state.mark_stale_loaders(timeout)

    def register_loader(self, loader_id: str, details: dict[str, Any]) -> None:
        if not loader_id or len(loader_id) > 128:
            raise ValueError("Invalid loader ID.")
        status = details.get("status", "unavailable")
        self._validate_loader_status(status)
        with self.state.lock:
            interrupted = any(
                assignment.get("loader_id") == loader_id and assignment.get("status") == "interrupted"
                for assignment in self.state.active_assignments.values()
            )
        if interrupted:
            status = "interrupted"
        self.state.register_loader(loader_id, {**details, "status": status})

    def update_loader(self, loader_id: str, details: dict[str, Any]) -> None:
        status = details.get("status")
        if status is not None:
            self._validate_loader_status(status)
            with self.state.lock:
                interrupted = any(
                    assignment.get("loader_id") == loader_id and assignment.get("status") == "interrupted"
                    for assignment in self.state.active_assignments.values()
                )
            if interrupted and status != "interrupted":
                raise ValueError("This loader has an interrupted order awaiting operator recovery.")
        self.state.update_loader(loader_id, details)

    def heartbeat(self, loader_id: str) -> None:
        self.state.update_loader(loader_id, {"last_seen": time.time()})

    def assignment_for_loader(self, loader_id: str) -> dict[str, Any] | None:
        with self.assignment_lock:
            return self._assignment_for_loader(loader_id)

    def _assignment_for_loader(self, loader_id: str) -> dict[str, Any] | None:
        assignment = self.state.assignment_for_loader(loader_id)
        if assignment and assignment.get("status") == "interrupted":
            return None
        if assignment and assignment.get("prepared") and Path(assignment.get("bundle_path", "")).is_file():
            return {
                **assignment["prepared"],
                "completed_units": assignment.get("completed_units", []),
            }
        if assignment is None:
            assignment = self.state.atomic_assign_order(loader_id)
        if assignment is None:
            return None
        order_id = assignment["order_id"]
        order = assignment["order"]
        try:
            tags = set(order.get("tags", []))
            tags.add("processing")
            if not self.shopify.update_order_tags(order_id, list(tags)):
                raise RuntimeError("Shopify did not confirm the processing tag.")
            self.state.set_assignment_tags(order_id, list(tags))
            prepared = self.workflow.prepare_order_for_loader(order)
            runtime_config = {
                key: self.config.get(key)
                for key in (
                    "packing_slip",
                    "add_packing_slip_to_order",
                    "print_mailing_label",
                    "queue_multi_print_orders",
                    "cleanup",
                    "company",
                )
                if key in self.config
            }
            prepared["runtime_config"] = runtime_config
            self.bundle_dir.mkdir(parents=True, exist_ok=True)
            bundle_name = hashlib.sha256(order_id.encode("utf-8")).hexdigest() + ".zip"
            bundle_path = self.bundle_dir / bundle_name
            bundle_path.write_bytes(self._make_bundle(prepared))
            response = {
                "order_id": order_id,
                "order_name": prepared["order_name"],
                "print_units": prepared["print_units"],
                "open_fulfillment_order_ids": prepared["open_fulfillment_order_ids"],
                "runtime_config": runtime_config,
                "bundle_available": True,
                "completed_units": [],
            }
            self.state.cache_assignment(order_id, response, str(bundle_path.resolve()))
            return response
        except Exception as error:
            self.rollback_assignment(order_id, str(error))
            raise

    def complete_assignment(self, loader_id: str, order_id: str, status: str) -> dict[str, Any]:
        self._validate_loader_status(status)
        if status not in ("ready", "unavailable"):
            raise ValueError("Completion status must be ready or unavailable.")
        with self.state.lock:
            assignment = self.state.active_assignments.get(order_id)
            bundle_path = assignment.get("bundle_path") if assignment else None
        completed = self.state.complete_assignment(order_id, status, loader_id)
        if bundle_path:
            Path(bundle_path).unlink(missing_ok=True)
        return completed

    def complete_print_unit(self, loader_id: str, order_id: str, unit_index: int) -> None:
        self.state.acknowledge_print_unit(loader_id, order_id, unit_index)

    def bundle_for_loader(self, loader_id: str) -> Path:
        assignment = self.state.assignment_for_loader(loader_id)
        if not assignment or assignment.get("status") != "processing" or not assignment.get("bundle_path"):
            raise FileNotFoundError("No prepared assignment bundle is available.")
        path = Path(assignment["bundle_path"])
        if not path.is_file():
            raise FileNotFoundError("The prepared assignment bundle is missing.")
        return path

    def report_interrupted(self, loader_id: str, order_id: str, reason: str) -> None:
        with self.state.lock:
            assignment = self.state.active_assignments.get(order_id)
            if assignment is None or assignment["loader_id"] != loader_id:
                raise ValueError("The order is not assigned to this loader.")
            assignment["status"] = "interrupted"
            self.state.registered_loaders[loader_id].update({
                "status": "interrupted",
                "order_id": order_id,
                "error": reason[:1000],
            })
            self.state._save_locked()

    def requeue_interrupted(self, order_id: str) -> None:
        with self.state.lock:
            assignment = self.state.active_assignments.get(order_id)
            if assignment is None or assignment.get("status") != "interrupted":
                raise ValueError("Only interrupted assignments can be requeued.")
            loader_id = assignment["loader_id"]
            order = assignment["order"]
            tags = [tag for tag in order.get("tags", []) if str(tag).lower() != "processing"]
            bundle_path = assignment.get("bundle_path")
        if not self.shopify.update_order_tags(order_id, tags):
            raise RuntimeError("Shopify did not confirm removal of the processing tag.")
        self.state.requeue_interrupted(order_id)
        if bundle_path:
            Path(bundle_path).unlink(missing_ok=True)

    def rollback_assignment(self, order_id: str, reason: str) -> None:
        with self.state.lock:
            assignment = self.state.active_assignments.get(order_id)
            order = assignment.get("order", {}) if assignment else {}
            tags = [tag for tag in order.get("tags", []) if str(tag).lower() != "processing"]
        try:
            self.shopify.update_order_tags(order_id, tags)
        finally:
            self.state.rollback_assignment(order_id, reason)

    @staticmethod
    def _validate_loader_status(status: str) -> None:
        if status not in ("ready", "busy", "unavailable", "interrupted"):
            raise ValueError("Loader status must be ready, busy, unavailable, or interrupted.")

    @staticmethod
    def _make_bundle(prepared: dict[str, Any]) -> bytes:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            manifest = {
                "version": 1,
                "order_id": prepared["order_id"],
                "order_name": prepared["order_name"],
                "print_units": prepared["print_units"],
                "open_fulfillment_order_ids": prepared["open_fulfillment_order_ids"],
                "runtime_config": prepared.get("runtime_config", {}),
            }
            archive.writestr("manifest.json", json.dumps(manifest))
            for unit, assets in zip(prepared["print_units"], prepared["assets_by_unit"]):
                unit_dir = f"units/{unit['index']}"
                archive.write(assets["picture"], f"{unit_dir}/picture.png")
                archive.write(assets["frame"], f"{unit_dir}/frame.png")
        return stream.getvalue()