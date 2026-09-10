import json
from pathlib import Path
from google.cloud import storage
from google.api_core.exceptions import Forbidden

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

    def download_blob_to_file(self, gcs_blob_name: str, local_destination_path: Path) -> Path:
        """Downloads a specific blob from GCS to local directory."""
        blob = self.bucket.blob(gcs_blob_name)
        if not blob.exists():
            raise FileNotFoundError(f"Blob '{gcs_blob_name}' not found in bucket '{self.bucket.name}'")
        
        local_destination_path.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(local_destination_path))
        return local_destination_path

    def read_json_blob(self, gcs_blob_name: str) -> dict:
        """Reads a JSON file directly into a Python dictionary without writing to disk."""
        blob = self.bucket.blob(gcs_blob_name)
        if not blob.exists():
            raise FileNotFoundError(f"JSON Blob '{gcs_blob_name}' not found in bucket.")
        
        content = blob.download_as_string()
        return json.loads(content.decode("utf-8"))

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
                job_dir / "packing_slip.png",
            ),
        }

        downloaded = {}
        for asset_type, (blob_name, destination) in asset_paths.items():
            try:
                blob = self.bucket.blob(blob_name)
                if not blob.exists():
                    raise FileNotFoundError(
                        f"GCS object was not found: gs://{self.bucket.name}/{blob_name}"
                    )
                blob.download_to_filename(str(destination))
            except FileNotFoundError:
                raise
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

    def fetch_order_assets(self, gcs_prefix: str, local_target_dir: Path) -> list[Path]:
        """Downloads all files starting with a supplied prefix into a target directory."""
        blobs = self.client.list_blobs(self.bucket, prefix=gcs_prefix)
        downloaded_files = []
        
        for blob in blobs:
            if blob.name.endswith("/"):  # Skip directory markers
                continue
            
            # Extract simple filename from path
            filename = Path(blob.name).name
            dest_file = local_target_dir / filename
            blob.download_to_filename(str(dest_file))
            downloaded_files.append(dest_file)
            
        return downloaded_files