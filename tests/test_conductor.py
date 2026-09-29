import json
import zipfile
import pytest

from modules.conductor import ConductorService
from modules.conductor_state import ConductorState


class FakeShopify:
    def __init__(self, pages):
        self.pages = list(pages)
        self.tag_updates = []
        self.fulfillment_updates = []

    def get_unfulfilled_orders(self):
        return self.pages.pop(0)

    def update_order_tags(self, order_id, tags):
        self.tag_updates.append((order_id, tags))
        return True

    def start_fulfillment_processing(self, order_id, fulfillment_order_ids):
        self.fulfillment_updates.append((order_id, fulfillment_order_ids))
        return True


class FakeWorkflow:
    def __init__(self, shopify, tmp_path):
        self.shopify = shopify
        picture = tmp_path / "picture.png"
        frame = tmp_path / "frame.png"
        picture.write_bytes(b"picture")
        frame.write_bytes(b"frame")
        self.prepared = {
            "order_id": "order-1",
            "order_name": "#1001",
            "print_units": [{"index": 0, "job_id": "job-1"}],
            "assets_by_unit": [{"picture": picture, "frame": frame}],
            "open_fulfillment_order_ids": ["fo-1"],
        }

    def prepare_order_for_loader(self, order):
        return self.prepared


def test_assignment_bundle_and_completion_shopify_reconciliation(tmp_path):
    order = {
        "id": "order-1",
        "name": "#1001",
        "tags": [],
        "displayFulfillmentStatus": "UNFULFILLED",
        "open_fulfillment_order_ids": ["fo-1"],
    }
    shopify = FakeShopify([[order], [order], []])
    workflow = FakeWorkflow(shopify, tmp_path)
    state = ConductorState(tmp_path / "conductor_state.json")
    conductor = ConductorService(workflow, state, {
        "orders_update_interval": 15,
        "packing_slip": True,
        "cleanup": True,
    })

    conductor.poll_once()
    conductor.register_loader("loader-1", {"status": "ready"})
    assignment = conductor.assignment_for_loader("loader-1")

    assert assignment["print_units"] == [{"index": 0, "job_id": "job-1"}]
    assert shopify.tag_updates == [("order-1", ["processing"])]
    assert shopify.fulfillment_updates == []
    assert assignment["bundle_available"] is True
    with zipfile.ZipFile(conductor.bundle_for_loader("loader-1")) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["order_id"] == "order-1"
        assert manifest["runtime_config"] == {"packing_slip": True, "cleanup": True}
        assert archive.read("units/0/picture.png") == b"picture"

    with pytest.raises(ValueError, match="every print unit"):
        conductor.complete_assignment("loader-1", "order-1", "unavailable")
    conductor.complete_print_unit("loader-1", "order-1", 0)
    completed = conductor.complete_assignment("loader-1", "order-1", "unavailable")
    assert conductor.complete_assignment("loader-1", "order-1", "unavailable") == completed
    conductor.poll_once()
    assert shopify.fulfillment_updates == [("order-1", ["fo-1"])]
    assert shopify.tag_updates[-1] == ("order-1", [])
    assert "order-1" in state.completed_orders

    conductor.poll_once()
    assert state.completed_orders == {}
    assert state.queued_orders == {}