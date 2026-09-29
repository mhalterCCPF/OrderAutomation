from pathlib import Path

import pytest

import modules.order_automation_cli as cli
import modules.order_workflow as workflow_module


class FakeGCS:
    def download_job_assets(self, job_id, assets_dir, design_name):
        job_dir = assets_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        files = {}
        for filename in ("picture.png", "frame.png", "packing_slip.png"):
            path = job_dir / filename
            path.write_bytes(f"{job_id}:{filename}".encode())
            files[filename.removesuffix(".png")] = path
        return files


class FakeShopify:
    def __init__(self):
        self.fetches = 0
        self.tags = []

    def get_and_lock_next_order(self):
        self.fetches += 1
        return {
            "id": "order-1",
            "name": "#1001",
            "tags": ["processing"],
            "lineItems": {"edges": [{"node": {
                "title": "Sample",
                "quantity": 2,
                "customAttributes": [{"key": "Job ID", "value": "job123"}],
            }}]},
        }

    def start_fulfillment_processing(self, order_id, fulfillment_order_ids):
        return True

    def update_order_tags(self, order_id, tags):
        self.tags.append((order_id, tags))
        return True


def _make_workflow(tmp_path, monkeypatch):
    shopify = FakeShopify()
    monkeypatch.setattr(workflow_module, "ShopifyService", lambda **_kwargs: shopify)
    monkeypatch.setattr(workflow_module, "GCSService", lambda **_kwargs: FakeGCS())
    config = {
        "shopify_shop_url": "test.myshopify.com",
        "shopify_access_token": "test-token",
        "gcs_bucket_name": "test-bucket",
        "downloaded_assets_dir": str(tmp_path / "assets"),
        "eufymake_dir": str(tmp_path / "printer"),
        "packing_slip": False,
        "cleanup": True,
    }
    return workflow_module.WorkflowOrchestrator(config), shopify


def test_prepare_resumes_manifest_and_complete_waits_for_every_unit(tmp_path, monkeypatch):
    workflow, shopify = _make_workflow(tmp_path, monkeypatch)

    prepared = workflow.prepare_next_order()
    assert prepared["status"] == "prepared"
    assert prepared["print_units"] == [
        {"index": 0, "job_id": "job123"},
        {"index": 1, "job_id": "job123"},
    ]
    assert workflow.prepare_next_order()["token"] == prepared["token"]
    assert shopify.fetches == 1

    workflow.stage_print_unit(prepared["token"], 0)
    with pytest.raises(ValueError, match="every print unit"):
        workflow.complete_order(prepared["token"])

    staged = workflow.stage_print_unit(prepared["token"], 1)
    assert [Path(path).name for path in staged["files"]] == ["picture.png", "frame.png"]
    assert (tmp_path / "printer" / "picture.png").read_bytes() == b"job123:picture.png"

    with pytest.raises(ValueError, match="every print unit has been printed"):
        workflow.complete_order(prepared["token"])
    workflow.complete_print_unit(prepared["token"], 0)
    workflow.complete_print_unit(prepared["token"], 1)

    assert workflow.complete_order(prepared["token"])["status"] == "completed"
    assert not (tmp_path / "assets" / "job123").exists()
    assert not (tmp_path / "assets" / ".orderautomation_pending_order.json").exists()
    assert shopify.tags[0][1] == ["files_ready"]


def test_cli_applies_nonsecret_overrides_but_keeps_secrets(tmp_path, monkeypatch):
    captured = {}

    class FakeWorkflow:
        def __init__(self, config):
            captured["config"] = config

        def prepare_next_order(self):
            return {"status": "no_orders"}

    monkeypatch.setattr(cli, "load_config", lambda: {
        "packing_slip": True,
        "company": {"name": "Saved", "email": "saved@example.com"},
    })
    monkeypatch.setattr(cli, "load_secrets", lambda: {
        "shopify_shop_url": "store.myshopify.com",
        "shopify_access_token": "secret",
        "gcs_creds_path": "credentials.json",
    })
    monkeypatch.setattr(cli, "WorkflowOrchestrator", FakeWorkflow)

    result = cli.execute_request({
        "version": 1,
        "action": "prepare_next_order",
        "config": {
            "packing_slip": False,
            "company": {"name": "Robot setting"},
        },
    })

    assert result == {"status": "no_orders"}
    assert captured["config"]["packing_slip"] is False
    assert captured["config"]["company"] == {
        "name": "Robot setting", "email": "saved@example.com"
    }
    assert captured["config"]["shopify_access_token"] == "secret"
    assert captured["config"]["gcs_creds_path"] == "credentials.json"


def test_cli_rejects_unsupported_protocol_and_config_keys(monkeypatch):
    with pytest.raises(ValueError, match="Unsupported protocol version"):
        cli.execute_request({"version": 2, "action": "prepare_next_order"})

    with pytest.raises(ValueError, match="Unsupported runtime config keys"):
        cli.execute_request({
            "version": 1,
            "action": "prepare_next_order",
            "config": {"shopify_access_token": "should-not-be-overridden"},
        })


def test_prepare_order_for_loader_expands_quantity_and_keeps_local_asset_paths(tmp_path):
    class FakeGCS:
        def download_job_assets(self, job_id, assets_dir, design_name):
            job_dir = assets_dir / job_id
            job_dir.mkdir(parents=True)
            result = {}
            for key in ("picture", "frame", "packing_slip"):
                path = job_dir / f"{key}.png"
                path.write_bytes(key.encode())
                result[key] = path
            return result

    class FakeShopify:
        pass

    workflow = workflow_module.WorkflowOrchestrator.__new__(workflow_module.WorkflowOrchestrator)
    workflow.config = {
        "downloaded_assets_dir": str(tmp_path / "assets"),
        "packing_slip": False,
    }
    workflow.gcs = FakeGCS()
    workflow.shopify = FakeShopify()
    order = {
        "id": "order-2",
        "name": "#1002",
        "lineItems": {"edges": [{"node": {
            "title": "Sample",
            "quantity": 2,
            "customAttributes": [{"key": "Job ID", "value": "job456"}],
        }}]},
    }

    prepared = workflow.prepare_order_for_loader(order)

    assert [unit["index"] for unit in prepared["print_units"]] == [0, 1]
    assert [unit["job_id"] for unit in prepared["print_units"]] == ["job456", "job456"]
    assert prepared["assets_by_unit"][0]["picture"].read_bytes() == b"picture"
    assert prepared["assets_by_unit"][1]["frame"].read_bytes() == b"frame"