$root = "C:\Users\micha\OrderAutomation"
$moduleNames = @(
    "config.py",
    "GCS_download.py",
    "gui_config.py",
    "order_workflow.py",
    "packing_slip_pdf.py",
    "shopify_service.py",
    "test_gcs_download.py"
)
foreach ($name in $moduleNames) {
    $src = Join-Path $root $name
    $dst = Join-Path (Join-Path $root "modules") $name
    if (Test-Path $src) {
        if (-not (Test-Path $dst)) {
            Move-Item -LiteralPath $src -Destination $dst -Force
        }
    }
}

$configNames = @("app_config.json", "packing_slip.html")
foreach ($name in $configNames) {
    $src = Join-Path $root $name
    $dst = Join-Path (Join-Path $root "config") $name
    if (Test-Path $src) {
        if (-not (Test-Path $dst)) {
            Move-Item -LiteralPath $src -Destination $dst -Force
        }
    }
}

$keyNames = @("secrets.json", "gcp_credentials.json")
foreach ($name in $keyNames) {
    $src = Join-Path $root $name
    $dst = Join-Path (Join-Path $root "keys") $name
    if (Test-Path $src) {
        if (-not (Test-Path $dst)) {
            Move-Item -LiteralPath $src -Destination $dst -Force
        }
    }
}

Write-Output "layout ready"
