import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT_DIR / "config"
KEYS_DIR = ROOT_DIR / "keys"

CONFIG_FILE = CONFIG_DIR / "app_config.json"
SECRETS_FILE = KEYS_DIR / "secrets.json"
GCP_CREDS_FILE = KEYS_DIR / "gcp_credentials.json"

DEFAULT_CONFIG = {
    "packing_slip": True,
    "print_mailing_label": True,
    "queue_multi_print_orders": False,
    "cleanup": False,
    "eufymake_dir": str(Path.home() / "Desktop" / "EufyHotFolder"),
    "downloaded_assets_dir": str(Path.home() / "Desktop" / "DownloadedAssets"),
    "packing_slip_dir": str(Path.home() / "Desktop" / "PackingSlips"),
    "gcs_bucket_name": "my-ccpf-assets-bucket",
    "company": {
        "name": "",
        "address_line1": "",
        "city_state_zip": "",
        "email": "",
        "website": "",
    },
}

DEFAULT_SECRETS = {
    "shopify_shop_url": "your-store.myshopify.com",
    "shopify_access_token": "shpat_REPLACE_ME",
}


def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    _ensure_dir(CONFIG_DIR)
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        saved_config = json.load(f)
    config = {**DEFAULT_CONFIG, **saved_config}
    config["company"] = {**DEFAULT_CONFIG["company"], **saved_config.get("company", {})}
    return config


def save_config(cfg: dict):
    _ensure_dir(CONFIG_DIR)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)


def load_secrets() -> dict:
    _ensure_dir(KEYS_DIR)
    if not SECRETS_FILE.exists():
        with open(SECRETS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_SECRETS, f, indent=4)
        secrets = DEFAULT_SECRETS.copy()
    else:
        with open(SECRETS_FILE, "r", encoding="utf-8") as f:
            secrets = {**DEFAULT_SECRETS, **json.load(f)}

    return {
        "shopify_shop_url": secrets["shopify_shop_url"],
        "shopify_access_token": secrets["shopify_access_token"],
        "gcs_creds_path": str(GCP_CREDS_FILE),
    }