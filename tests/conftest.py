import pytest


@pytest.fixture
def order_snapshot():
    return {
        "id": "order-1",
        "name": "#1001",
        "createdAt": "2026-10-01T12:00:00Z",
        "email": "order@example.com",
        "phone": "555-0100",
        "customer": {
            "displayName": "GCS Customer",
            "email": "customer@example.com",
            "phone": "555-0101",
        },
        "shippingAddress": {
            "name": "GCS Recipient",
            "address1": "123 Example St",
            "city": "Example City",
            "province": "CA",
            "zip": "90210",
            "country": "USA",
        },
        "shippingLines": [{"title": "Express"}],
        "lineItems": [{
            "id": "line-1",
            "title": "GCS Product",
            "sku": "EXAMPLE",
            "quantity": 2,
            "variantTitle": "Example Variant",
            "customAttributes": [
                {"key": "Job ID", "value": "job123"},
                {"key": "_design", "value": "custom_design"},
                {"key": "Size", "value": "6x8"},
            ],
        }],
    }
