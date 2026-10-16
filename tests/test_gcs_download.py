from pathlib import Path
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import Forbidden, NotFound

from modules.gcs_download import GCSService


class FakeBlob:
    def __init__(self, name: str, missing: bool = False):
        self.name = name
        self.missing = missing

    def exists(self):
        raise AssertionError("downloads must not pre-check existence (extra GCS request)")

    def download_to_filename(self, destination: str):
        if self.missing:
            raise NotFound("missing")
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-data")


class BucketStub:
    def __init__(self, missing: set[str] = frozenset()):
        self.name = "demo-bucket"
        self.calls = []
        self.missing = missing

    def blob(self, name: str):
        self.calls.append(name)
        return FakeBlob(name, name in self.missing)


def test_download_job_assets_uses_order_design_value(tmp_path):
    service = GCSService.__new__(GCSService)
    service.bucket = BucketStub()

    result = service.download_job_assets("job123", tmp_path, "custom_design.png")

    assert result["frame"].name == "frame.png"
    assert result["picture"].name == "picture.png"
    assert result["packing_slip"].name == "custom_design_thumbnail.png"
    assert (tmp_path / "job123" / "frame.png").exists()
    assert (tmp_path / "job123" / "picture.png").exists()
    assert (tmp_path / "job123" / "custom_design_thumbnail.png").exists()
    assert service.bucket.calls == [
        "frame2print/job123/custom_design_frame_with_transparency.png",
        "frame2print/job123/custom_design_picture_with_transparency.png",
        "frame2print/job123/custom_design_thumbnail.png",
    ]


def test_download_job_assets_preserves_png_extension(tmp_path):
    service = GCSService.__new__(GCSService)
    service.bucket = BucketStub()

    service.download_job_assets("job123", tmp_path, "custom_design.PNG")

    assert service.bucket.calls == [
        "frame2print/job123/custom_design_frame_with_transparency.png",
        "frame2print/job123/custom_design_picture_with_transparency.png",
        "frame2print/job123/custom_design_thumbnail.png",
    ]


def test_download_job_assets_reports_missing_object_path(tmp_path):
    service = GCSService.__new__(GCSService)
    missing = "frame2print/job123/custom_design_picture_with_transparency.png"
    service.bucket = BucketStub({missing})

    with pytest.raises(FileNotFoundError, match=f"gs://demo-bucket/{missing}"):
        service.download_job_assets("job123", tmp_path, "custom_design")

def test_packing_slip_uses_picture_downloaded_from_gcs(tmp_path, monkeypatch, order_snapshot):
    import modules.order_workflow as workflow_module

    class FakeGCS:
        def read_json_blob(self, path):
            assert path == "frame2print/job123/order_metadata.json"
            return order_snapshot

        def download_job_assets(self, job_id, assets_dir, design_name):
            job_dir = assets_dir / job_id
            job_dir.mkdir(parents=True)
            frame = job_dir / "frame.png"
            picture = job_dir / "picture.png"
            packing_slip = job_dir / "packing_slip.png"
            frame.write_bytes(b"frame")
            picture.write_bytes(b"high-resolution picture")
            packing_slip.write_bytes(b"composited packing slip image")
            return {"frame": frame, "picture": picture, "packing_slip": packing_slip}

    class FakeShopify:
        def update_order_tags(self, order_id, tags):
            pass

        def attach_pdf_metafield(self, order_id, pdf_path):
            pass

    captured = {}

    def fake_generate_packing_slip_pdf(order, config):
        captured["order"] = order
        return tmp_path / "packing-slip.pdf"

    monkeypatch.setattr(workflow_module, "generate_packing_slip_pdf", fake_generate_packing_slip_pdf)

    order = {
        "name": "#SHOPIFY-ROUTING",
        "lineItems": {
            "edges": [{
                "node": {
                    "id": "line-1",
                    "customAttributes": [{"key": "Job ID", "value": "job123"}],
                }
            }]
        },
    }

    orchestrator = workflow_module.WorkflowOrchestrator.__new__(workflow_module.WorkflowOrchestrator)
    orchestrator.config = {
        "downloaded_assets_dir": str(tmp_path / "assets"),
        "packing_slip": True,
        "queue_multi_print_orders": False,
        "cleanup": False,
    }
    orchestrator.gcs = FakeGCS()
    orchestrator.shopify = FakeShopify()

    success, message = orchestrator._execute_pipeline({**order, "id": "order-1"})

    assert success, message
    assert captured["order"]["line_items"][0]["image_path"].startswith("file:/")
    assert captured["order"]["line_items"][0]["image_path"].endswith("job123/packing_slip.png")
    assert captured["order"]["name"] == "#1001"
    assert captured["order"]["email"] == "order@example.com"
    assert captured["order"]["phone"] == "555-0100"
    assert captured["order"]["customer_name"] == "GCS Customer"
    assert captured["order"]["shipping_address"]["address1"] == "123 Example St"
    assert captured["order"]["created_at"] == "2026-10-01T12:00:00Z"
    assert captured["order"]["shipping_method"] == "Express"
    item = captured["order"]["line_items"][0]
    assert (item["title"], item["quantity"], item["variant_title"]) == ("GCS Product", 2, "Example Variant")
    assert item["custom_attributes"]["Size"] == "6x8"
    assert set(captured["order"]["line_items"][0]) == {
        "id",
        "title",
        "sku",
        "variant_title",
        "quantity",
        "custom_attributes",
        "image_path",
        "job_id",
        "design",
    }


@pytest.mark.parametrize("content", [b"broken", b"[]", b"null", b"\xff"])
def test_read_json_blob_rejects_invalid_metadata(content):
    service = GCSService.__new__(GCSService)
    service.bucket = Mock(name="bucket")
    service.bucket.name = "demo-bucket"
    service.bucket.blob.return_value.download_as_bytes.return_value = content
    with pytest.raises(ValueError, match="gs://demo-bucket/frame2print/job123/order_metadata.json"):
        service.read_json_blob("frame2print/job123/order_metadata.json")


@pytest.mark.parametrize("error, expected", [(NotFound("missing"), FileNotFoundError), (Forbidden("denied"), PermissionError)])
def test_read_json_blob_reports_missing_or_denied_metadata(error, expected):
    service = GCSService.__new__(GCSService)
    service.bucket = Mock()
    service.bucket.name = "demo-bucket"
    service.bucket.blob.return_value.download_as_bytes.side_effect = error
    with pytest.raises(expected, match="gs://demo-bucket/frame2print/job123/order_metadata.json"):
        service.read_json_blob("frame2print/job123/order_metadata.json")


def test_read_json_blob_returns_snapshot(order_snapshot):
    service = GCSService.__new__(GCSService)
    service.bucket = Mock()
    service.bucket.blob.return_value.download_as_bytes.return_value = json.dumps(order_snapshot).encode()
    assert service.read_json_blob("frame2print/job123/order_metadata.json") == order_snapshot


def make_workflow(tmp_path, snapshot):
    from modules.order_workflow import WorkflowOrchestrator

    workflow = WorkflowOrchestrator.__new__(WorkflowOrchestrator)
    workflow.config = {"downloaded_assets_dir": str(tmp_path)}
    workflow.gcs = Mock()
    workflow.gcs.read_json_blob.return_value = snapshot
    workflow.gcs.download_job_assets.return_value = {
        "frame": tmp_path / "frame.png",
        "picture": tmp_path / "picture.png",
        "packing_slip": tmp_path / "thumbnail.png",
    }
    return workflow


def routing_order(snapshot):
    return {
        "id": snapshot["id"],
        "name": snapshot["name"],
        "lineItems": {"edges": [{"node": {
            "id": item["id"],
            "customAttributes": [{"key": "Job ID", "value": item["job_id"]}],
        }} for item in snapshot["line_items"]]},
    }


def add_line(snapshot, line_id, job_id, quantity=1):
    line = deepcopy(snapshot["line_items"][0])
    line.update(id=line_id, job_id=job_id, quantity=quantity)
    line["custom_attributes"]["Job ID"] = job_id
    snapshot["line_items"].append(line)


def test_multiple_jobs_read_one_complete_snapshot(tmp_path, order_snapshot):
    add_line(order_snapshot, "line-2", "job456", quantity=3)
    workflow = make_workflow(tmp_path, order_snapshot)
    workflow.shopify = Mock()
    result = workflow.prepare_order_for_loader(routing_order(order_snapshot))
    assert [unit["job_id"] for unit in result["print_units"]] == ["job123"] * 2 + ["job456"] * 3
    workflow.gcs.read_json_blob.assert_called_once_with("frame2print/job123/order_metadata.json")
    assert [call.args[:1] for call in workflow.gcs.download_job_assets.call_args_list] == [("job123",), ("job456",)]
    workflow.shopify.start_fulfillment_processing.assert_not_called()


def test_shared_job_id_downloads_assets_once(tmp_path, order_snapshot):
    add_line(order_snapshot, "line-2", "job123")
    workflow = make_workflow(tmp_path, order_snapshot)
    normalized, _, jobs, items = workflow._download_order_assets(routing_order(order_snapshot))
    assert len(normalized["line_items"]) == len(items) == 2
    assert jobs == {"job123"}
    workflow.gcs.read_json_blob.assert_called_once()
    workflow.gcs.download_job_assets.assert_called_once()


@pytest.mark.parametrize("change", [
    {"version": None}, {"version": 2}, {"id": "wrong-order"}, {"name": None}, {"name": " "},
    {"line_items": []}, {"line_items": {}}, {"shipping_address": "invalid"},
    {"shipping_address": {"zip": 90210}}, {"email": 5}, {"customer_name": []}, {"shipping_method": 1},
])
def test_metadata_requires_valid_identity_and_structure(tmp_path, order_snapshot, change):
    route = routing_order(order_snapshot)
    workflow = make_workflow(tmp_path, {**order_snapshot, **change})
    with pytest.raises(ValueError, match="order_metadata.json"):
        workflow._download_order_assets(route)
    workflow.gcs.download_job_assets.assert_not_called()


@pytest.mark.parametrize("change", [
    {"id": "wrong-line"}, {"id": ""}, {"title": None}, {"quantity": 0}, {"quantity": True}, {"quantity": "2"},
    {"job_id": "wrong-job"}, {"job_id": None}, {"design": None}, {"design": ""}, {"sku": 5},
    {"custom_attributes": [{"key": "Size", "value": "6x8"}]}, {"custom_attributes": {"Size": 6}},
])
def test_metadata_requires_matching_line_and_valid_print_fields(tmp_path, order_snapshot, change):
    route = routing_order(order_snapshot)
    order_snapshot["line_items"][0].update(change)
    workflow = make_workflow(tmp_path, order_snapshot)
    with pytest.raises(ValueError):
        workflow._download_order_assets(route)
    workflow.gcs.download_job_assets.assert_not_called()


def test_metadata_line_items_must_cover_whole_shopify_order(tmp_path, order_snapshot):
    route = routing_order(order_snapshot)
    add_line(order_snapshot, "line-2", "job123")
    workflow = make_workflow(tmp_path, order_snapshot)
    with pytest.raises(ValueError, match="do not match the Shopify order"):
        workflow._download_order_assets(route)
    add_line(order_snapshot, "line-2", "job123")
    with pytest.raises(ValueError, match="unique"):
        workflow._download_order_assets(routing_order(order_snapshot))


def test_missing_metadata_has_no_shopify_fallback(tmp_path, order_snapshot):
    workflow = make_workflow(tmp_path, order_snapshot)
    workflow.gcs.read_json_blob.side_effect = FileNotFoundError("missing order_metadata.json")
    workflow.shopify = Mock()
    with pytest.raises(FileNotFoundError, match="order_metadata.json"):
        workflow.prepare_order_for_loader(routing_order(order_snapshot))
    assert workflow.shopify.mock_calls == []
    workflow.gcs.download_job_assets.assert_not_called()


@pytest.mark.parametrize("job_attribute", [[], [{"key": "Job ID", "value": "../escape"}]])
def test_missing_or_invalid_job_id_fails_before_gcs(tmp_path, order_snapshot, job_attribute):
    add_line(order_snapshot, "line-2", "job456")
    route = routing_order(order_snapshot)
    route["lineItems"]["edges"][1]["node"]["customAttributes"] = job_attribute
    workflow = make_workflow(tmp_path, order_snapshot)
    with pytest.raises(ValueError, match="Job"):
        workflow.prepare_order_for_loader(route)
    workflow.gcs.read_json_blob.assert_not_called()
    workflow.gcs.download_job_assets.assert_not_called()


@pytest.mark.parametrize("entrypoint", ["process_next_order", "process_specific_order"])
def test_standalone_and_specific_order_paths_load_gcs(tmp_path, order_snapshot, entrypoint):
    workflow = make_workflow(tmp_path, order_snapshot)
    workflow.shopify = Mock()
    route = routing_order(order_snapshot)
    route["tags"] = ["processing"]
    workflow.shopify.get_and_lock_next_order.return_value = route
    workflow.shopify.get_specific_order.return_value = route
    arguments = ("1001",) if entrypoint == "process_specific_order" else ()
    success, message = getattr(workflow, entrypoint)(*arguments)
    assert success, message
    workflow.gcs.read_json_blob.assert_called_once_with("frame2print/job123/order_metadata.json")
    workflow.shopify.update_order_tags.assert_called_with("order-1", ["files_ready"])
    assert workflow.shopify.update_order_tags.call_count == 1 + (entrypoint == "process_specific_order")
    assert workflow.shopify.start_fulfillment_processing.call_count == (entrypoint == "process_next_order")


def test_optional_contact_data_defaults_for_rendering(tmp_path, order_snapshot):
    workflow = make_workflow(tmp_path, order_snapshot)
    for field in ("created_at", "email", "phone", "customer_name", "shipping_address", "shipping_method"):
        order_snapshot[field] = None
    normalized = workflow._validate_snapshot(order_snapshot, "order-1")
    assert normalized["email"] is normalized["phone"] is None
    assert normalized["created_at"] == normalized["customer_name"] == normalized["shipping_method"] == ""
    assert normalized["shipping_address"] == {}


def test_gcs_snapshot_renders_packing_slip_pdf(tmp_path, order_snapshot):
    from modules.packing_slip_pdf import generate_packing_slip_pdf

    workflow = make_workflow(tmp_path, order_snapshot)
    normalized = workflow._validate_snapshot(order_snapshot, "order-1")
    output = generate_packing_slip_pdf(normalized, {"packing_slip_dir": str(tmp_path)})
    assert output == tmp_path / "PackingSlip_1001.pdf"
    assert output.read_bytes().startswith(b"%PDF")