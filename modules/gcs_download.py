import json
from pathlib import Path
from google.cloud import storage
from google.api_core.exceptions import Forbidden, NotFound

class GCSService:
    def __init__(self, bucket_name: str, credentials_path: str = None):
        """
        :param bucket_name: GCS Bucket Name
        :param credentials_path: Path to GCP service account key JSON (optional if GOOGLE_APPLICATION_CREDENTIALS set)
        """
        if not bucket_name:
            raise ValueError("GCS bucket name is not configured.")

        if credentials_path:
            credentials_file = Path(credentials_path)
            if not credentials_file.is_file():
                raise FileNotFoundError(
                    f"GCS credentials file was not found: {credentials_file}"
                )
            self.client = storage.Client.from_service_account_json(str(credentials_file))
        else:
            self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def read_json_blob(self, gcs_blob_name: str) -> dict:
        """Reads a JSON file directly into a Python dictionary without writing to disk."""
        blob = self.bucket.blob(gcs_blob_name)
        location = f"gs://{self.bucket.name}/{gcs_blob_name}"
        try:
            content = blob.download_as_bytes()
        except NotFound as error:
            raise FileNotFoundError(f"GCS metadata was not found: {location}") from error
        except Forbidden as error:
            raise PermissionError(
                f"GCS access was denied for {location}. Grant the service account Storage Object Viewer."
            ) from error
        try:
            data = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"GCS metadata is not valid UTF-8 JSON: {location}") from error
        if not isinstance(data, dict):
            raise ValueError(f"GCS metadata must contain a JSON object: {location}")
        return data

    def download_job_assets(
        self,
        job_id: str,
        local_assets_dir: Path,
        design_name: str | None = None,
    ) -> dict[str, Path]:
        """Download the composited packing-slip image and high-resolution print assets."""
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            raise ValueError("Cannot download GCS assets without a job ID.")

        design_value = str(design_name or "").strip()
        if not design_value:
            raise ValueError("Cannot download GCS assets without a design name.")
        design_stem = design_value[:-4] if design_value.lower().endswith(".png") else design_value
        job_dir = local_assets_dir / normalized_job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        asset_paths = {
            "frame": (
                f"frame2print/{normalized_job_id}/{design_stem}_frame_with_transparency.png",
                job_dir / "frame.png",
            ),
            "picture": (
                f"frame2print/{normalized_job_id}/{design_stem}_picture_with_transparency.png",
                job_dir / "picture.png",
            ),
            "packing_slip": (
                f"frame2print/{normalized_job_id}/{design_stem}_thumbnail.png",
                job_dir / f"{design_stem}_thumbnail.png",
            ),
        }

        downloaded = {}
        for asset_type, (blob_name, destination) in asset_paths.items():
            try:
                self.bucket.blob(blob_name).download_to_filename(str(destination))
            except NotFound as error:
                raise FileNotFoundError(
                    f"GCS object was not found: gs://{self.bucket.name}/{blob_name}"
                ) from error
            except Forbidden as error:
                raise PermissionError(
                    "GCS access was denied for "
                    f"gs://{self.bucket.name}/{blob_name}. Grant the service account "
                    "the Storage Object Viewer role on this bucket, and verify that "
                    "the object exists at this exact path."
                ) from error
            except Exception as error:
                raise RuntimeError(
                    f"GCS download failed for gs://{self.bucket.name}/{blob_name}: "
                    f"{type(error).__name__}: {error}"
                ) from error

            if not destination.is_file() or destination.stat().st_size == 0:
                raise IOError(
                    f"GCS download produced an empty or missing file: {destination}"
                )
            downloaded[asset_type] = destination
        return downloaded
