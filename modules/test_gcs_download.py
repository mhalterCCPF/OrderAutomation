from pathlib import Path

from GCS_download import GCSService


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

    result = service.download_job_assets("job123", tmp_path, "custom_design")

    assert result["frame"].name == "frame.png"
    assert result["picture"].name == "picture.png"
    assert (tmp_path / "job123" / "frame.png").exists()
    assert (tmp_path / "job123" / "picture.png").exists()
    assert service.bucket.calls == [
        "frame2print/job123/custom_design_frame_with_transparency.png",
        "frame2print/job123/custom_design_picture_with_transparency.png",
    ]
