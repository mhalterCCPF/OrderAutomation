from modules.shopify_service import ShopifyService


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