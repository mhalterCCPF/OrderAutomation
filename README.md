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
- `modules/order_automation_cli.py` — versioned JSON interface for headless integrations
- `modules/shopify_service.py` — Shopify API calls
- `modules/gcs_download.py` — GCS download logic
- `modules/packing_slip_pdf.py` — packing slip generation
- `modules/gui_config.py` — configuration dialog UI
- `modules/conductor.py`, `modules/conductor_state.py`, and `modules/conductor_server.py` — Conductor polling, state, and LAN API
- `modules/conductor_gui.py` — Conductor window
- `tests/` — order workflow, Shopify snapshot, Conductor, and API tests

## Headless Integration

The desktop application remains the normal standalone entry point. Other local programs can invoke OrderAutomation without opening Tkinter by running `python -m modules.order_automation_cli` from this repository and sending one JSON request on stdin. The command writes one JSON response to stdout; errors use status `error` and exit code 1.

Protocol version 1 supports `prepare_next_order`, `stage_print_unit`, `complete_print_unit`, and `complete_order`. Preparation locks and downloads an order, then returns an ordered print-unit manifest and a resumable token. Each unit is staged just before printing; the caller acknowledges a unit only after its physical print cycle succeeds. Completion updates the order tag and performs configured cleanup. A pending order is returned again by the next prepare call, including its staged and physically completed unit indexes.

Requests may include a `config` object containing non-secret keys from `DEFAULT_CONFIG` in `modules/config.py`. Runtime values override saved app settings only for that process; Shopify credentials and GCP credentials always come from OrderAutomation's own `keys/` files. The robot integration stores those runtime settings in its own ignored `config.json`, so the repositories remain independently configurable.

The existing `print_mailing_label` option is currently only a placeholder. It is accepted as a configuration value but does not generate or print a label.

Example prepare request:

```json
{"version":1,"action":"prepare_next_order","config":{"packing_slip":true,"cleanup":true}}
```

## Conductor Mode

Click **Conductor** in the desktop launcher to start Shopify polling and the authenticated loader API. `orders_update_interval` is measured in minutes and defaults to 15. The Conductor binds to `conductor_host` and `conductor_port` (default `0.0.0.0:8765`); allow that port only on the trusted private LAN in the host firewall. Do not expose the service to the public internet.

The Conductor creates a shared bearer token in `keys/conductor_token.txt`; provide the host's LAN address, port, and token to each eufyLoaderRobot Listen prompt. Keep the token private. The Conductor stores `queued_orders`, `active_assignments`, `completed_orders`, and `registered_loaders` in memory protected by a thread lock and atomically saves them to the ignored `conductor_state.json` file. This coordinates threads in one Conductor process; do not run multiple Conductor instances against the same Shopify store.

Assignment applies the Shopify `processing` tag to keep the standalone Get Next Order action from claiming the same order, but fulfillment remains Unfulfilled until the loader reports successful physical printing. Conductor retries Shopify's fulfillment-progress update on subsequent polls while a completed order still appears Unfulfilled; it removes the local completed record after a later snapshot no longer includes that order. An interrupted assignment is not automatically reassigned. Select it in Active Assignments and explicitly requeue it only after reviewing the physical print state.

**Pause Conductor** suspends Shopify polling only; the window, API, loader heartbeats, and order dispatch stay active. **Stop Conductor** closes the Conductor session and stops polling/new dispatch while preserving active assignments. Neither control immediately stops robot motion.

### First Conductor Run

1. Complete the existing Shopify/GCS setup and choose the intended GCS bucket. Start the robot dashboard separately, connect to its controller, Home, and mark at least one shelf ready.
2. In **Set Configurations**, set **Orders Update Interval (minutes)**. The default is `15`; use a shorter value such as `3` only for supervised testing.
3. Click **Conductor** and confirm expected orders appear in **Queued Orders**. The window shows its API port and shared token. Use `localhost` as the host on the same computer; use the Conductor computer's actual LAN IP or hostname from another computer. `0.0.0.0` is a bind address, not a client destination.
4. On eufyLoaderRobot, click **Listen** and enter the host, port, and token. Keep the API port restricted to a trusted private LAN.
5. Monitor **Active Assignments**, **Completed Orders not yet in Shopify**, and **Registered eufyLoaders**. Completed orders remain locally pending while Shopify still reports Unfulfilled; Conductor retries fulfillment progress and removes the local record after a later poll no longer includes the order.

Orders are dispatched in ascending Shopify creation order. The `processing` tag prevents standalone **Get Next Order** from claiming an assigned order but does not change fulfillment status. Manually resetting a printed order to Unfulfilled makes it eligible to be queued again once its completion record has cleared; only do this intentionally for testing. Interrupted assignments remain locked until an operator checks the physical state and explicitly requeues them.

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

- `orders_update_interval`: Shopify poll interval in minutes; defaults to `15`.
- `conductor_host` / `conductor_port`: private-LAN API bind address and port; defaults to `0.0.0.0` and `8765`.
- `loader_heartbeat_timeout`: seconds before a loader with no heartbeat is marked unavailable/interrupted; defaults to `30`.
- `packing_slip`: Enables or disables packing slip PDF generation for each processed order.
- `add_packing_slip_to_order`: If enabled, the generated packing slip is attached to the Shopify order as a metafield.
- `print_mailing_label`: Reserved placeholder; no mailing-label generation is implemented.
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
