from modules.shopify_service import ShopifyService
import pytest
import re


def test_unfulfilled_order_snapshot_paginates_without_mutating_orders():
    service = ShopifyService.__new__(ShopifyService)
    calls = []
    pages = [
        {
            "orders": {
                "edges": [{
                    "node": {
                        "id": "order-1",
                        "tags": [],
                        "displayFulfillmentStatus": "UNFULFILLED",
                        "fulfillmentOrders": {"edges": [{"node": {"id": "fo-1"}}]},
                    }
                }],
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
            }
        },
        {
            "orders": {
                "edges": [{
                    "node": {
                        "id": "order-2",
                        "tags": ["processing"],
                        "displayFulfillmentStatus": "IN_PROGRESS",
                        "fulfillmentOrders": {"edges": []},
                    }
                }, {
                    "node": {
                        "id": "order-3",
                        "tags": [],
                        "displayFulfillmentStatus": "UNFULFILLED",
                        "fulfillmentOrders": {"edges": [{"node": {"id": "fo-3"}}]},
                    }
                }],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        },
    ]

    def execute(query, variables):
        calls.append((query, variables))
        return pages.pop(0)

    service._execute = execute

    orders = service.get_unfulfilled_orders()

    assert [order["id"] for order in orders] == ["order-1", "order-3"]
    assert orders[0]["open_fulfillment_order_ids"] == ["fo-1"]
    assert orders[1]["open_fulfillment_order_ids"] == ["fo-3"]
    assert calls[0][1] == {"after": None}
    assert calls[1][1] == {"after": "cursor-1"}
    assert "mutation" not in calls[0][0].lower()


@pytest.mark.parametrize("method, arguments", [
    ("get_unfulfilled_orders", ()),
    ("get_and_lock_next_order", ()),
    ("get_specific_order", ("1001",)),
])
def test_intake_queries_request_only_operational_fields(method, arguments):
    service = ShopifyService.__new__(ShopifyService)
    calls = []

    def execute(query, variables=None):
        calls.append(query)
        return {"orders": {"edges": [], "pageInfo": {"hasNextPage": False}}}

    service._execute = execute
    getattr(service, method)(*arguments)
    query = calls[0]
    for field in ("email", "phone", "customer", "shippingAddress", "title", "sku", "quantity", "variant", "tagnams"):
        assert not re.search(rf"\b{field}\b", query), field
    for field in ("id", "name", "tags", "lineItems", "customAttributes"):
        assert re.search(rf"\b{field}\b", query), field


def test_locking_and_fulfillment_operations_remain_available():
    service = ShopifyService.__new__(ShopifyService)
    calls = []

    def execute(query, variables=None):
        calls.append((query, variables))
        if "GetNextUnfulfilledOrder" in query:
            return {"orders": {"edges": [{"node": {
                "id": "order-1", "name": "#1001", "tags": ["existing"],
                "displayFulfillmentStatus": "UNFULFILLED",
                "fulfillmentOrders": {"edges": [{"node": {"id": "fo-1"}}]},
            }}]}}
        if "UpdateOrderTags" in query:
            return {"orderUpdate": {"userErrors": []}}
        return {"fulfillmentOrderReportProgress": {"userErrors": []}}

    service._execute = execute
    order = service.get_and_lock_next_order()
    assert order["tags"] == ["existing", "processing"]
    assert order["open_fulfillment_order_ids"] == ["fo-1"]
    assert service.start_fulfillment_processing(order["id"], order["open_fulfillment_order_ids"])
    assert calls[1][1] == {"input": {"id": "order-1", "tags": ["existing", "processing"]}}
    assert calls[2][1] == {"id": "fo-1"}