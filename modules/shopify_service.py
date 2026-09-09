import requests

class ShopifyService:
    def __init__(self, shop_url: str, access_token: str, api_version: str = "2026-07"):
        """
        :param shop_url: e.g., 'your-store.myshopify.com'
        :param access_token: Admin API Access Token (shpat_...)
        """
        self.endpoint = f"https://{shop_url}/admin/api/{api_version}/graphql.json"
        self.headers = {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": access_token
        }

    def _execute(self, query: str, variables: dict = None) -> dict:
        response = requests.post(
            self.endpoint,
            headers=self.headers,
            json={"query": query, "variables": variables or {}}
        )
        response.raise_for_status()
        res_json = response.json()
        if "errors" in res_json:
            raise Exception(f"Shopify GraphQL Error: {res_json['errors']}")
        return res_json.get("data", {})

    def get_and_lock_next_order(self) -> dict | None:
        """
        Queries open, unfulfilled orders (excluding in-progress ones), 
        appends a 'processing' tag to lock the order, and returns it.
        """
        query = """
        query GetNextUnfulfilledOrder {
          orders(
            first: 10, 
            query: "status:open AND fulfillment_status:unfulfilled AND -tag:processing AND -tag:files_ready", 
            sortKey: ORDER_NUMBER, 
            reverse: false
          ) {
            edges {
              node {
                id
                name
                legacyResourceId
                createdAt
                tags
                displayFulfillmentStatus
                email
                phone
                customer {
                  displayName
                  firstName
                  lastName
                  email
                  phone
                }
                shippingAddress {
                  name
                  firstName
                  lastName
                  company
                  address1
                  address2
                  city
                  province
                  provinceCode
                  zip
                  country
                  countryCode
                  phone
                }
                customAttributes { key value }
                lineItems(first: 100) {
                  edges {
                    node {
                      id
                      title
                      sku
                      quantity
                      variant { title }
                      customAttributes { key value }
                    }
                  }
                }
              }
            }
          }
        }
        """
        data = self._execute(query)
        edges = data.get("orders", {}).get("edges", [])

        for edge in edges:
            candidate = edge["node"]

            # Ensure the order is strictly UNFULFILLED (not IN_PROGRESS or PARTIALLY_FULFILLED)
            if candidate.get("displayFulfillmentStatus") == "UNFULFILLED":
                order_id = candidate["id"]
                current_tags = candidate.get("tags", [])

                # Lock the order by appending 'processing'
                new_tags = current_tags + ["processing"]
                if self.update_order_tags(order_id, new_tags):
                    candidate["tags"] = new_tags
                    return candidate

        return None

    def get_specific_order(self, order_number: str) -> dict | None:
        """Fetch a specific order by order name/number (e.g., '1001' or '#1001')."""
        formatted_num = order_number if order_number.startswith("#") else f"#{order_number}"
        query = """
        query GetSpecificOrder($queryStr: String!) {
          orders(first: 1, query: $queryStr) {
            edges {
              node {
                id
                name
                createdAt
                tags
                email
                phone
                customer {
                  displayName
                  firstName
                  lastName
                  email
                  phone
                }
                shippingAddress {
                  name
                  firstName
                  lastName
                  company
                  address1
                  address2
                  city
                  province
                  provinceCode
                  zip
                  country
                  countryCode
                  phone
                }
                customAttributes { key value }
                lineItems(first: 100) {
                  edges {
                    node {
                      id
                      title
                      sku
                      quantity
                      variant { title }
                      customAttributes { key value }
                    }
                  }
                }
              }
            }
          }
        }
        """
        data = self._execute(query, {"queryStr": f"name:'{formatted_num}'"})
        edges = data.get("orders", {}).get("edges", [])
        return edges[0]["node"] if edges else None

    def update_order_tags(self, order_id: str, tags: list[str]) -> bool:
        """Updates order tags cleanly."""
        mutation = """
        mutation UpdateOrderTags($input: OrderInput!) {
          orderUpdate(input: $input) {
            order { id tags }
            userErrors { field message }
          }
        }
        """
        res = self._execute(mutation, {"input": {"id": order_id, "tags": tags}})
        errors = res.get("orderUpdate", {}).get("userErrors", [])
        return len(errors) == 0

    def start_fulfillment_processing(self, order_id: str) -> bool:
        """Moves the order's open fulfillment orders to the 'IN_PROGRESS' status."""
        query = """
        query GetOpenFulfillmentOrders($orderId: ID!) {
          order(id: $orderId) {
            fulfillmentOrders(first: 10, query: "status:open") {
              edges { node { id } }
            }
          }
        }
        """
        data = self._execute(query, {"orderId": order_id})
        edges = data.get("order", {}).get("fulfillmentOrders", {}).get("edges", [])

        mutation = """
        mutation ReportFulfillmentOrderProgress($id: ID!) {
          fulfillmentOrderReportProgress(id: $id) {
            fulfillmentOrder { id status }
            userErrors { field message }
          }
        }
        """
        success = True
        for edge in edges:
            res = self._execute(mutation, {"id": edge["node"]["id"]})
            errors = res.get("fulfillmentOrderReportProgress", {}).get("userErrors", [])
            success = success and len(errors) == 0
        return success


    def attach_pdf_metafield(self, order_id: str, pdf_file_path: str):
        """
        Uploads local PDF to Shopify Staged Uploads and links file reference
        to an Order Metafield (custom.order_summary_pdf).
        """
        # Step 1: Request Staged Upload Target
        staged_mutation = """
        mutation StagedUploadsCreate($input: [StagedUploadInput!]!) {
          stagedUploadsCreate(input: $input) {
            stagedTargets { resourceUrl url parameters { name value } }
          }
        }
        """
        filename = pdf_file_path.split("/")[-1]
        staged_res = self._execute(staged_mutation, {
            "input": [{
                "filename": filename,
                "mimeType": "application/pdf",
                "httpMethod": "POST",
                "resource": "FILE"
            }]
        })
        target = staged_res["stagedUploadsCreate"]["stagedTargets"][0]
        
        # Step 2: Upload file bytes to the target AWS/Shopify bucket
        upload_url = target["url"]
        form_data = {p["name"]: p["value"] for p in target["parameters"]}
        with open(pdf_file_path, "rb") as f:
            requests.post(upload_url, data=form_data, files={"file": f})

        # Step 3: Register File in Shopify Files
        file_create_mutation = """
        mutation FileCreate($files: [FileCreateInput!]!) {
          fileCreate(files: $files) {
            files { id fileStatus }
          }
        }
        """
        target_resource_url = target["resourceUrl"]
        file_res = self._execute(file_create_mutation, {
            "files": [{
                "originalSource": target_resource_url,
                "contentType": "FILE"
            }]
        })
        file_gid = file_res["fileCreate"]["files"][0]["id"]

        # Step 4: Set File Reference on Order Metafield
        metafield_mutation = """
        mutation SetMetafields($metafields: [MetafieldsSetInput!]!) {
          metafieldsSet(metafields: $metafields) {
            userErrors { field message }
          }
        }
        """
        self._execute(metafield_mutation, {
            "metafields": [{
                "ownerId": order_id,
                "namespace": "custom",
                "key": "order_summary_pdf",
                "type": "file_reference",
                "value": file_gid
            }]
        })