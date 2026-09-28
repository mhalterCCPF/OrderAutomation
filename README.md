# OrderAutomation

OrderAutomation is a locally run Windows desktop app that pulls open orders from Shopify, extracts the Job_ID from each line item, downloads the matching image assets from a Google Cloud Storage bucket, and stages them for processing or printing. It can also generate a packing slip PDF and attach it back to the Shopify order when enabled.

This project is designed for a small print or fulfillment workflow where a local workstation handles order intake, asset retrieval, and print staging without needing a hosted backend.

## Overview

The app uses:

- Shopify GraphQL for order lookup and order tagging updates
- Google Cloud Storage for asset retrieval by Job_ID
- Tkinter for the desktop interface
- WeasyPrint for packing slip PDF generation
- a local virtual environment for Python dependencies

At a high level, the workflow is:

1. Query Shopify for open, unfulfilled orders.
2. Lock the order by applying a processing tag.
3. Read the Job_ID custom attribute for each line item.
4. Download the matching design assets from GCS.
5. Optionally generate and attach a packing slip PDF.
6. Stage print-ready files in a local folder for printing or fulfillment.

## Repository structure

- `app.py` — GUI launcher for the desktop app
- `setup_and_run.bat` — Windows setup script that installs prerequisites and launches the app
- `requirements.txt` — Python dependencies
- `requirements-dev.txt` — additional dependencies for running tests
- `config/app_config.json` — app behavior settings
- `keys/secrets.json` — Shopify credentials and local secret values (never commit; see Security section)
- `keys/secrets.example.json` — placeholder template for `keys/secrets.json`
- `keys/gcp_credentials.json` — Google Cloud service account credentials (never commit; see Security section)
- `keys/gcp_credentials.example.json` — placeholder template for `keys/gcp_credentials.json`
- `modules/config.py` — config and secret loading logic
- `modules/order_workflow.py` — main order-processing orchestration
- `modules/shopify_service.py` — Shopify API calls
- `modules/gcs_download.py` — GCS download logic
- `modules/packing_slip_pdf.py` — packing slip generation
- `modules/gui_config.py` — configuration dialog UI
- `tests/test_gcs_download.py` — unit tests for GCS/order asset handling

## Installation

### 1. Install Python

Install Python 3.12 on Windows and make sure it is available in PATH.

- Download: https://www.python.org/downloads/windows/
- Recommended version: Python 3.12.x

Verify the install:

```powershell
python --version
```

### 2. Install GTK for WeasyPrint

This app generates PDFs with WeasyPrint, which depends on GTK/Cairo libraries on Windows.

The easiest option is to use the included setup script, which installs the required tooling automatically. Alternatively, you can install MSYS2 and GTK manually.

Install MSYS2, then open a MSYS2 terminal and run:

```bash
pacman -S --noconfirm mingw-w64-x86_64-gtk3 mingw-w64-x86_64-pango mingw-w64-x86_64-gobject-introspection
```

After installation, ensure this folder is in PATH:

```text
C:\msys64\mingw64\bin
```

### 3. Create the virtual environment

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks script execution, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 4. Configure app settings and secrets

Edit these files before running the app:

- `config/app_config.json`
- `keys/secrets.json`
- `keys/gcp_credentials.json`

At minimum, you should provide:

- Shopify store URL and access token
- GCS bucket name
- local print and asset directories
- company branding details for the packing slip

### 5. Run the app

Start the app normally with:

```powershell
.\.venv\Scripts\python.exe app.py
```

Or launch it as a GUI-only process without a console window:

```powershell
.\.venv\Scripts\pythonw.exe app.py
```

The repo also includes `setup_and_run.bat`, which will check for Python, install MSYS2/GTK if missing, create the virtual environment, install dependencies, and launch the app automatically.

## Windows shortcut using pythonw

For a clean desktop launch, create a shortcut to the Python windowless launcher in the venv:

- Target: `C:\Users\<YourUser>\OrderAutomation\.venv\Scripts\pythonw.exe`
- Arguments: `app.py`
- Start in: `C:\Users\<YourUser>\OrderAutomation`

This keeps the app running as a normal desktop application without opening a console window.

## Configuration roles in `modules/config.py`

The app loads default settings from `DEFAULT_CONFIG` and default secrets from `DEFAULT_SECRETS` in `modules/config.py`.

- `packing_slip`: Enables or disables packing slip PDF generation for each processed order.
- `add_packing_slip_to_order`: If enabled, the generated packing slip is attached to the Shopify order as a metafield.
- `print_mailing_label`: Controls whether mailing-label or label-print functionality is used in the workflow.
- `queue_multi_print_orders`: Lets the app queue multiple print jobs in sequence instead of handling only one at a time.
- `cleanup`: Deletes downloaded job asset folders after the job is processed to keep the local directory clean.
- `eufymake_dir`: Points to the folder where print-ready image files are staged for an external printing app.
- `downloaded_assets_dir`: Stores temporary asset folders downloaded from GCS before they are cleaned up or consumed.
- `packing_slip_dir`: Directory where generated packing slip PDFs are saved.
- `gcs_bucket_name`: Name of the Google Cloud Storage bucket containing the job image assets.
- `company.name`: Company name displayed on the packing slip.
- `company.address_line1`: Primary company address line on the packing slip.
- `company.city_state_zip`: City, state, and ZIP text used in the packing slip.
- `company.email`: Contact email shown on the packaging output.
- `company.website`: Website URL used in the packing slip branding.

The secret values include:

- `shopify_shop_url`: Your Shopify store domain, such as `your-store.myshopify.com`
- `shopify_access_token`: The Shopify Admin API access token used for GraphQL queries and updates
- `gcs_creds_path`: The path to the Google Cloud credentials JSON file; by default this is set to the project’s `keys/gcp_credentials.json`

## Security

- Never commit `keys/secrets.json` or `keys/gcp_credentials.json` — both are excluded via `.gitignore`. Use `keys/secrets.example.json` and `keys/gcp_credentials.example.json` as templates when setting up a new environment.
- If either file is ever accidentally committed or shared, rotate the Shopify access token in the Shopify admin and revoke/rotate the GCP service account key immediately, then verify with `git log --all --full-history -- keys/secrets.json keys/gcp_credentials.json` whether history needs to be rewritten.

## Development / Testing

Install test dependencies and run the test suite from the project root:

```powershell
pip install -r requirements-dev.txt
pytest tests/ -v
```

## Notes

- The app expects a valid GCP service account key JSON file at `keys/gcp_credentials.json` unless you override the path.
- This project is meant to run locally on a Windows workstation rather than in a cloud environment.
- If the required runtime components are missing, `setup_and_run.bat` is the recommended way to install them.
