import pytest


@pytest.fixture
def order_snapshot():
    """Packing-slip snapshot written by the API's /shopify_webhook_order_paid endpoint."""
    return {
        "version": 1,
        "id": "order-1",
        "name": "#1001",
        "created_at": "2026-10-01T12:00:00Z",
        "email": "order@example.com",
        "phone": "555-0100",
        "customer_name": "GCS Customer",
        "shipping_address": {
            "name": "GCS Recipient",
            "address1": "123 Example St",
            "address2": None,
            "city": "Example City",
            "province": "CA",
            "zip": "90210",
            "country": "USA",
        },
        "shipping_method": "Express",
        "line_items": [{
            "id": "line-1",
            "title": "GCS Product",
            "sku": "EXAMPLE",
            "variant_title": "Example Variant",
            "quantity": 2,
            "custom_attributes": {"Job ID": "job123", "Size": "6x8"},
            "job_id": "job123",
            "design": "custom_design",
        }],
    }