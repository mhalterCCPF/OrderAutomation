from pathlib import Path

from modules.GCS_download import GCSService


class FakeBlob:
    def __init__(self, name: str):
        self.name = name

    def exists(self):
        return True

    def download_to_filename(self, destination: str):
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-data")


class BucketStub:
    def __init__(self):
        self.name = "demo-bucket"
        self.calls = []

    def blob(self, name: str):
        self.calls.append(name)
        return FakeBlob(name)


def test_download_job_assets_uses_order_design_value(tmp_path):
    service = GCSService.__new__(GCSService)
    service.bucket = BucketStub()

    result = service.download_job_assets("job123", tmp_path, "custom_design.png")

    assert result["frame"].name == "frame.png"
    assert result["picture"].name == "picture.png"
    assert result["packing_slip"].name == "packing_slip.png"
    assert (tmp_path / "job123" / "frame.png").exists()
    assert (tmp_path / "job123" / "picture.png").exists()
    assert (tmp_path / "job123" / "packing_slip.png").exists()
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


def test_packing_slip_uses_picture_downloaded_from_gcs(tmp_path, monkeypatch):
    import modules.order_workflow as workflow_module

    class FakeGCS:
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
        "name": "#1001",
        "createdAt": "2024-01-01",
        "customer": {"displayName": "Test User"},
        "shippingAddress": {"address1": "123 Main St", "city": "Anytown", "province": "CA", "zip": "90210", "country": "USA"},
        "customAttributes": [{"key": "_design", "value": "custom_design"}],
        "lineItems": {
            "edges": [{
                "node": {
                    "title": "Sample product",
                    "quantity": 2,
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

    success, message = orchestrator._execute_pipeline({**order, "id": "gid://shopify/Order/1001"})

    assert success, message
    assert captured["order"]["line_items"][0]["image_path"].startswith("file:/")
    assert captured["order"]["line_items"][0]["image_path"].endswith("job123/packing_slip.png")
    assert set(captured["order"]["line_items"][0]) == {
        "title",
        "sku",
        "variant_title",
        "quantity",
        "custom_attributes",
        "image_path",
        "job_id",
        "design",
    }
