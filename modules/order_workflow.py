import json
import os
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
            normalized_order, assets_dir, processed_job_ids, missing_job_items, items_with_assets = (
                self._download_order_assets(order)
            )
            print_units = []
            for item, _downloaded_assets in items_with_assets:
                job_id = item["job_id"]
                quantity = int(item.get("quantity") or 1)
                if quantity < 1:
                    raise ValueError(f"Invalid print quantity for {item.get('title') or job_id}.")
                print_units.extend({"index": len(print_units), "job_id": job_id} for _ in range(quantity))

            if missing_job_items:
                raise ValueError(
                    "No Job_ID custom attribute was found for: "
                    f"{', '.join(missing_job_items)}. GCS downloads were not complete."
                )
            if not print_units:
                raise ValueError("The order contains no line items with a Job_ID custom attribute.")

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
        normalized_order, _assets_dir, _processed_job_ids, missing_job_items, items_with_assets = (
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

        if missing_job_items:
            raise ValueError(
                "No Job_ID custom attribute was found for: "
                f"{', '.join(missing_job_items)}. GCS downloads were not complete."
            )
        if not print_units:
            raise ValueError("The order contains no line items with a Job_ID custom attribute.")

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
            normalized_order, assets_dir, processed_job_ids, missing_job_items, _items_with_assets = (
                self._download_order_assets(order)
            )

            if missing_job_items:
                item_names = ", ".join(missing_job_items)
                raise ValueError(
                    "No Job_ID custom attribute was found for: "
                    f"{item_names}. GCS downloads were not complete."
                )

            if not processed_job_ids:
                raise ValueError("The order contains no line items with a Job_ID custom attribute.")

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
        normalized_order = self._normalize_order(order)
        assets_dir = Path(self.config["downloaded_assets_dir"])
        processed_job_ids = set()
        missing_job_items = []
        items_with_assets = []

        for item in normalized_order["line_items"]:
            job_id = item.get("job_id")
            if not job_id:
                missing_job_items.append(item.get("title") or "Unnamed item")
                continue
            design_name = item.get("design") or normalized_order.get("design")
            downloaded = self.gcs.download_job_assets(job_id, assets_dir, design_name)
            item["image_path"] = downloaded["packing_slip"].resolve().as_uri()
            processed_job_ids.add(job_id)
            items_with_assets.append((item, downloaded))

        return normalized_order, assets_dir, processed_job_ids, missing_job_items, items_with_assets

    def _normalize_order(self, order: dict) -> dict:
        customer = order.get("customer") or {}
        customer_name = customer.get("displayName") or " ".join(
            part for part in (customer.get("firstName"), customer.get("lastName")) if part
        )
        shipping_address = order.get("shippingAddress") or {}
        order_attrs = {
            attribute["key"]: attribute.get("value", "")
            for attribute in order.get("customAttributes", [])
        }
        order_design = next(
            (
                str(value).strip()
                for key, value in order_attrs.items()
                if str(key).strip().lower() == "_design"
            ),
            None,
        )
        items = []
        for edge in order.get("lineItems", {}).get("edges", []):
            raw_item = edge["node"]
            attributes = {
                attribute["key"]: attribute.get("value", "")
                for attribute in raw_item.get("customAttributes", [])
            }
            job_id = next(
                (
                    str(value).strip()
                    for key, value in attributes.items()
                    if str(key).strip().lower().replace("_", " ").replace("-", " ") == "job id"
                ),
                None,
            )
            item_design = next(
                (
                    str(value).strip()
                    for key, value in attributes.items()
                    if str(key).strip().lower() == "_design"
                ),
                order_design,
            )
            items.append({
                "title": raw_item.get("title", ""),
                "sku": raw_item.get("sku"),
                "variant_title": (raw_item.get("variant") or {}).get("title"),
                "quantity": raw_item.get("quantity", 1),
                "custom_attributes": attributes,
                "image_path": "",
                "job_id": job_id,
                "design": item_design,
            })

        return {
            "name": order.get("name", ""),
            "created_at": order.get("createdAt", ""),
            "email": order.get("email") or customer.get("email"),
            "phone": order.get("phone") or customer.get("phone"),
            "customer_name": customer_name,
            "shipping_address": shipping_address,
            "design": order_design,
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