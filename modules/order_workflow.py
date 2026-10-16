import json
import os
import re
import stat
import uuid
from pathlib import Path
from shutil import copyfile
from typing import Callable

from modules.gcs_download import GCSService
from modules.packing_slip_pdf import generate_packing_slip_pdf
from modules.shopify_service import ShopifyService


class WorkflowOrchestrator:
    def __init__(self, config: dict, print_message_callback: Callable[[str], None] | None = None):
        self.config = config
        self.print_message_callback = print_message_callback
        self.shopify = ShopifyService(
            shop_url=config["shopify_shop_url"],
            access_token=config["shopify_access_token"]
        )
        self.gcs = GCSService(
            bucket_name=config["gcs_bucket_name"],
            credentials_path=config.get("gcs_creds_path")
        )

    def process_next_order(self) -> tuple[bool, str]:
        order = self.shopify.get_and_lock_next_order()
        if not order:
            return False, "No unprinted orders found."
        return self._execute_pipeline(order, mark_in_progress=True)

    def prepare_next_order(self) -> dict:
        state_path = self._pending_state_path()
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            return self._prepared_result(state)

        order = self.shopify.get_and_lock_next_order()
        if not order:
            return {"status": "no_orders", "message": "No unprinted orders found."}

        order_id = order["id"]
        try:
            normalized_order, assets_dir, processed_job_ids, items_with_assets = (
                self._download_order_assets(order)
            )
            print_units = []
            for item, _downloaded_assets in items_with_assets:
                job_id = item["job_id"]
                quantity = int(item.get("quantity") or 1)
                if quantity < 1:
                    raise ValueError(f"Invalid print quantity for {item.get('title') or job_id}.")
                print_units.extend({"index": len(print_units), "job_id": job_id} for _ in range(quantity))

            if self.config.get("packing_slip", False):
                pdf_path = generate_packing_slip_pdf(normalized_order, self.config)
                if self.config.get("add_packing_slip_to_order", True):
                    self.shopify.attach_pdf_metafield(order_id, str(pdf_path))

            self.shopify.start_fulfillment_processing(
                order_id, order.get("open_fulfillment_order_ids")
            )

            state = {
                "version": 1,
                "token": uuid.uuid4().hex,
                "order_id": order_id,
                "order_name": order.get("name", "").replace("#", ""),
                "order_tags": order.get("tags", []),
                "job_ids": sorted(processed_job_ids),
                "print_units": print_units,
                "staged_units": [],
                "completed_units": [],
                "assets_dir": str(assets_dir.resolve()),
                "eufymake_dir": str(Path(self.config["eufymake_dir"]).resolve()),
                "cleanup": bool(self.config.get("cleanup", False)),
            }
            self._write_pending_state(state_path, state)
            return self._prepared_result(state)
        except Exception:
            current_tags = set(order.get("tags", []))
            current_tags.discard("processing")
            current_tags.add("error_downloading")
            self.shopify.update_order_tags(order_id, list(current_tags))
            raise

    def prepare_order_for_loader(self, order: dict) -> dict:
        """Download one centrally queued order for transfer to a robot loader."""
        normalized_order, _assets_dir, _processed_job_ids, items_with_assets = (
            self._download_order_assets(order)
        )
        print_units = []
        assets_by_unit = []
        for item, downloaded in items_with_assets:
            job_id = item["job_id"]
            quantity = int(item.get("quantity") or 1)
            if quantity < 1:
                raise ValueError(f"Invalid print quantity for {item.get('title') or job_id}.")
            for _ in range(quantity):
                print_units.append({
                    "index": len(print_units),
                    "job_id": job_id,
                    "title": item.get("title", ""),
                })
                assets_by_unit.append({
                    "picture": downloaded["picture"],
                    "frame": downloaded["frame"],
                })

        if self.config.get("packing_slip", False):
            pdf_path = generate_packing_slip_pdf(normalized_order, self.config)
            if self.config.get("add_packing_slip_to_order", True):
                self.shopify.attach_pdf_metafield(order["id"], str(pdf_path))

        return {
            "order_id": order["id"],
            "order_name": order.get("name", ""),
            "print_units": print_units,
            "assets_by_unit": assets_by_unit,
            "open_fulfillment_order_ids": order.get("open_fulfillment_order_ids", []),
        }

    def stage_print_unit(self, token: str, unit_index: int) -> dict:
        state_path, state = self._load_pending_state(token)
        if not isinstance(unit_index, int) or not 0 <= unit_index < len(state["print_units"]):
            raise ValueError("Print unit index is out of range.")
        unit = state["print_units"][unit_index]
        source_dir = Path(state["assets_dir"]) / unit["job_id"]
        destination_dir = Path(state["eufymake_dir"])
        destination_dir.mkdir(parents=True, exist_ok=True)
        destinations = []
        for filename in ("picture.png", "frame.png"):
            destination = destination_dir / filename
            self._force_copy(source_dir / filename, destination)
            destinations.append(str(destination.resolve()))

        staged = set(state.get("staged_units", []))
        staged.add(unit_index)
        state["staged_units"] = sorted(staged)
        self._write_pending_state(state_path, state)
        return {"status": "staged", "token": token, "unit_index": unit_index, "files": destinations}

    def complete_order(self, token: str) -> dict:
        state_path, state = self._load_pending_state(token)
        required = set(range(len(state["print_units"])))
        if set(state.get("completed_units", [])) != required:
            raise ValueError("Cannot complete an order until every print unit has been printed.")

        tags = set(state.get("order_tags", []))
        tags.discard("processing")
        tags.add("files_ready")
        if not self.shopify.update_order_tags(state["order_id"], list(tags)):
            raise RuntimeError("Shopify did not confirm the completed order tags.")

        if state.get("cleanup"):
            for job_id in state["job_ids"]:
                self._cleanup_job_assets(Path(state["assets_dir"]), job_id)
        state_path.unlink()
        return {"status": "completed", "order_name": state["order_name"]}

    def complete_print_unit(self, token: str, unit_index: int) -> dict:
        state_path, state = self._load_pending_state(token)
        if not isinstance(unit_index, int) or not 0 <= unit_index < len(state["print_units"]):
            raise ValueError("Print unit index is out of range.")
        if unit_index not in state.get("staged_units", []):
            raise ValueError("Cannot complete a print unit before its files are staged.")
        completed = set(state.get("completed_units", []))
        completed.add(unit_index)
        state["completed_units"] = sorted(completed)
        self._write_pending_state(state_path, state)
        return {"status": "unit_completed", "token": token, "unit_index": unit_index}

    def _pending_state_path(self) -> Path:
        assets_dir = Path(self.config["downloaded_assets_dir"])
        assets_dir.mkdir(parents=True, exist_ok=True)
        return assets_dir / ".orderautomation_pending_order.json"

    @staticmethod
    def _write_pending_state(path: Path, state: dict) -> None:
        temporary_path = path.with_suffix(".tmp")
        temporary_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary_path.replace(path)

    def _load_pending_state(self, token: str) -> tuple[Path, dict]:
        path = self._pending_state_path()
        if not path.exists():
            raise ValueError("No prepared order is available to resume.")
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("token") != token:
            raise ValueError("The prepared order token does not match the pending order.")
        return path, state

    @staticmethod
    def _prepared_result(state: dict) -> dict:
        if state.get("version") != 1:
            raise ValueError("The pending order state uses an unsupported version.")
        return {
            "status": "prepared",
            "token": state["token"],
            "order_name": state["order_name"],
            "print_units": state["print_units"],
            "staged_units": state.get("staged_units", []),
            "completed_units": state.get("completed_units", []),
        }

    def process_specific_order(self, order_num: str) -> tuple[bool, str]:
        order = self.shopify.get_specific_order(order_num)
        if not order:
            return False, f"Order {order_num} not found."

        tags = list(set(order.get("tags", []) + ["processing"]))
        self.shopify.update_order_tags(order["id"], tags)
        return self._execute_pipeline(order, mark_in_progress=False)

    def _execute_pipeline(self, order: dict, mark_in_progress: bool = False) -> tuple[bool, str]:
        order_name = order["name"].replace("#", "")
        order_id = order["id"]

        try:
            normalized_order, assets_dir, processed_job_ids, _items_with_assets = (
                self._download_order_assets(order)
            )

            if self.config.get("packing_slip", False):
                pdf_path = generate_packing_slip_pdf(normalized_order, self.config)
                if self.config.get("add_packing_slip_to_order", True):
                    self.shopify.attach_pdf_metafield(order_id, str(pdf_path))

            if mark_in_progress:
                self.shopify.start_fulfillment_processing(
                    order_id, order.get("open_fulfillment_order_ids")
                )

            if self.config.get("queue_multi_print_orders", False):
                self._queue_prints(normalized_order, assets_dir)

            if self.config.get("cleanup", False):
                for job_id in processed_job_ids:
                    self._cleanup_job_assets(assets_dir, job_id)

            current_tags = set(order.get("tags", []))
            current_tags.discard("processing")
            current_tags.add("files_ready")
            self.shopify.update_order_tags(order_id, list(current_tags))
            return True, f"Successfully staged Order #{order_name}"

        except Exception as error:
            current_tags = set(order.get("tags", []))
            current_tags.discard("processing")
            current_tags.add("error_downloading")
            self.shopify.update_order_tags(order_id, list(current_tags))
            return False, (
                f"Error processing Order #{order_name} "
                f"({type(error).__name__}): {error}"
            )

    def _download_order_assets(self, order: dict):
        """Pair Shopify's routed line items with the webhook snapshot in GCS and download their assets.

        Every design folder holds the same full-order snapshot, so one read covers the whole order.
        """
        routed_items = []
        for edge in order.get("lineItems", {}).get("edges", []):
            raw_item = edge["node"]
            line_id = raw_item.get("id")
            if not line_id or any(line_id == routed_id for routed_id, _ in routed_items):
                raise ValueError("Shopify routing requires unique line-item IDs.")
            job_id = self._job_id(self._attributes(raw_item.get("customAttributes", [])))
            if not job_id:
                raise ValueError(f"No Job_ID custom attribute was found for Shopify line item {line_id}.")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
                raise ValueError(f"Invalid Job ID for Shopify line item {line_id}.")
            routed_items.append((line_id, job_id))
        if not routed_items:
            raise ValueError("The order contains no line items with a Job_ID custom attribute.")

        metadata_path = f"frame2print/{routed_items[0][1]}/order_metadata.json"
        try:
            normalized_order = self._validate_snapshot(self.gcs.read_json_blob(metadata_path), order["id"])
        except ValueError as error:
            raise ValueError(f"Invalid GCS metadata at {metadata_path}: {error}") from error
        snapshot_items = {item["id"]: item for item in normalized_order["line_items"]}
        if set(snapshot_items) != {line_id for line_id, _ in routed_items}:
            raise ValueError(f"GCS metadata line items do not match the Shopify order: {metadata_path}")

        assets_dir = Path(self.config["downloaded_assets_dir"])
        downloaded_assets = {}
        items_with_assets = []
        for line_id, job_id in routed_items:
            item = snapshot_items[line_id]
            if item["job_id"] != job_id or not item["design"]:
                raise ValueError(f"GCS metadata does not match the routed line item {line_id}: {metadata_path}")
            asset_key = (job_id, item["design"])
            if asset_key not in downloaded_assets:
                downloaded_assets[asset_key] = self.gcs.download_job_assets(job_id, assets_dir, item["design"])
            downloaded = downloaded_assets[asset_key]
            item["image_path"] = downloaded["packing_slip"].resolve().as_uri()
            items_with_assets.append((item, downloaded))
        normalized_order["line_items"] = [item for item, _ in items_with_assets]
        processed_job_ids = {job_id for _, job_id in routed_items}
        return normalized_order, assets_dir, processed_job_ids, items_with_assets

    @staticmethod
    def _attributes(attributes: list) -> dict:
        if not isinstance(attributes, list) or any(
            not isinstance(attribute, dict)
            or not isinstance(attribute.get("key"), str)
            or not isinstance(attribute.get("value"), str)
            for attribute in attributes
        ):
            raise ValueError("customAttributes must be a list of string key/value objects.")
        return {attribute["key"]: attribute["value"] for attribute in attributes}

    @staticmethod
    def _job_id(attributes: dict) -> str | None:
        return next(
            (
                value.strip()
                for key, value in attributes.items()
                if key.strip().lower().replace("_", " ").replace("-", " ") == "job id"
            ),
            None,
        )

    @staticmethod
    def _optional_strings(values: dict, fields: tuple, label: str):
        for field in fields:
            if values.get(field) is not None and not isinstance(values[field], str):
                raise ValueError(f"{label}{field} must be a string or null.")

    def _validate_snapshot(self, snapshot: dict, order_id: str) -> dict:
        """Validate the packing-slip snapshot written by /shopify_webhook_order_paid (version 1)."""
        if snapshot.get("version") != 1:
            raise ValueError(f"Unsupported snapshot version {snapshot.get('version')!r}.")
        if snapshot.get("id") != order_id:
            raise ValueError("Order ID does not match Shopify.")
        if not isinstance(snapshot.get("name"), str) or not snapshot["name"].strip():
            raise ValueError("Order name is required.")
        self._optional_strings(snapshot, ("created_at", "email", "phone", "customer_name", "shipping_method"), "")
        shipping_address = snapshot.get("shipping_address")
        if shipping_address is not None and (
            not isinstance(shipping_address, dict)
            or any(part is not None and not isinstance(part, str) for part in shipping_address.values())
        ):
            raise ValueError("shipping_address must be an object of string/null values or null.")
        raw_items = snapshot.get("line_items")
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("line_items must be a non-empty array.")
        items = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict) or not isinstance(raw_item.get("id"), str) or not raw_item["id"]:
                raise ValueError("Each line item requires an ID.")
            if any(item["id"] == raw_item["id"] for item in items):
                raise ValueError("Line-item IDs must be unique.")
            if not isinstance(raw_item.get("title"), str):
                raise ValueError("Each line item requires a title.")
            quantity = raw_item.get("quantity")
            if type(quantity) is not int or quantity < 1:
                raise ValueError("Each line item requires a positive integer quantity.")
            self._optional_strings(raw_item, ("sku", "variant_title", "job_id", "design"), "Line-item ")
            attributes = raw_item.get("custom_attributes") or {}
            if not isinstance(attributes, dict) or any(
                not isinstance(key, str) or not isinstance(value, str) for key, value in attributes.items()
            ):
                raise ValueError("Line-item custom_attributes must be an object of strings.")
            items.append({
                "id": raw_item["id"],
                "title": raw_item["title"],
                "sku": raw_item.get("sku"),
                "variant_title": raw_item.get("variant_title"),
                "quantity": quantity,
                "custom_attributes": attributes,
                "image_path": "",
                "job_id": raw_item.get("job_id"),
                "design": raw_item.get("design"),
            })

        return {
            "name": snapshot["name"],
            "created_at": snapshot.get("created_at") or "",
            "email": snapshot.get("email"),
            "phone": snapshot.get("phone"),
            "customer_name": snapshot.get("customer_name") or "",
            "shipping_address": shipping_address or {},
            "shipping_method": snapshot.get("shipping_method") or "",
            "line_items": items,
        }
    def _queue_prints(self, order: dict, assets_dir: Path):
        eufymake_dir = Path(self.config["eufymake_dir"])
        eufymake_dir.mkdir(parents=True, exist_ok=True)

        print_jobs = []
        for item in order["line_items"]:
            job_id = item.get("job_id")
            if not job_id:
                continue
            quantity = int(item.get("quantity") or 1)
            print_jobs.extend([job_id] * quantity)

        total_prints = len(print_jobs)
        for print_number, job_id in enumerate(print_jobs, start=1):
            source_dir = assets_dir / job_id
            self._force_copy(source_dir / "picture.png", eufymake_dir / "picture.png")
            self._force_copy(source_dir / "frame.png", eufymake_dir / "frame.png")
            # Pause between every staged file (across items and quantities) so the
            # previous print isn't overwritten before it's actually been printed.
            if print_number < total_prints:
                self._notify_print(
                    f"Print file for {job_id} staged ({print_number} of {total_prints} total prints). "
                    "Print it, then click OK to stage the next print file."
                )

    @staticmethod
    def _force_copy(source: Path, destination: Path):
        if destination.exists():
            os.chmod(destination, stat.S_IWRITE)
            destination.unlink()
        copyfile(source, destination)

    def _notify_print(self, message: str):
        if self.print_message_callback:
            self.print_message_callback(message)

    @staticmethod
    def _cleanup_job_assets(assets_dir: Path, job_id: str):
        job_dir = assets_dir / job_id
        if job_dir.exists():
            for path in job_dir.iterdir():
                if path.is_file() or path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    WorkflowOrchestrator._remove_directory(path)
            job_dir.rmdir()

    @staticmethod
    def _remove_directory(directory: Path):
        for path in directory.iterdir():
            if path.is_dir():
                WorkflowOrchestrator._remove_directory(path)
            else:
                path.unlink()
        directory.rmdir()