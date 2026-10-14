from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import csv
import tempfile
from unittest.mock import Mock, patch
from uuid import uuid4
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIRequestFactory

from .models import Dealer, Order, Product
from .views import create_order, order_action
from supplier_mock.mock_supplier import SupplierMock, build_public_feed


class OrderInventoryFlowTests(TransactionTestCase):
    def setUp(self):
        self.dealer = Dealer.objects.create(
            dealer_code="TEST-DEALER",
            name="Inventory Flow Test Dealer",
        )
        self.product = Product.objects.create(
            sku="TEST-STOCK-001",
            name="Inventory Flow Test Product",
            price=Decimal("100.00"),
            stock=8,
        )

    def test_draft_does_not_deduct_stock_but_confirmation_does(self):
        response = self.client.post(
            "/api/orders/",
            data={
                "dealer_id": self.dealer.pk,
                "items": [{"sku": self.product.sku, "quantity": 3}],
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        order_number = response.json()["order_number"]
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)

        response = self.client.post(
            f"/api/orders/{order_number}/confirm/",
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "CONFIRMED")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

        response = self.client.post(
            f"/api/orders/{order_number}/cancel/",
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "CANCELLED")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)

        response = self.client.post(
            f"/api/orders/{order_number}/cancel/",
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)


class AssistantFallbackTests(TransactionTestCase):
    @override_settings(
        GEMINI_API_KEY="",
        OPENAI_API_KEY="",
        ANTHROPIC_API_KEY="",
        GROQ_API_KEY="",
    )
    def test_fallback_prefers_part_and_vehicle_match(self):
        dealer = Dealer.objects.create(
            dealer_code="ASSISTANT-TEST",
            name="Assistant Test Dealer",
        )
        Product.objects.create(
            sku="BRK-1001",
            name="Brake Shoe Set - Suzuki Gixxer SF",
            category="Brakes",
            vehicle_fitment="Suzuki Gixxer SF",
            price=Decimal("500.00"),
            stock=4,
        )
        Product.objects.create(
            sku="BRK-1044",
            name="Brake Pad Set - Bajaj Pulsar 150",
            category="Brakes",
            vehicle_fitment="Bajaj Pulsar 150",
            price=Decimal("1459.50"),
            stock=136,
        )

        response = self.client.post(
            "/api/assistant/chat/",
            data={
                "session_id": str(uuid4()),
                "dealer_id": dealer.pk,
                "message": "Do you have brake pads for a Pulsar 150?",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["cited_skus"], ["BRK-1044"])
        self.assertIn("Brake Pad Set - Bajaj Pulsar 150", body["reply"])
        self.assertIn("₹1459.50", body["reply"])
        self.assertIn("available stock: 136", body["reply"])
        self.assertEqual(
            [call["name"] for call in body["tool_calls"]],
            ["search_parts", "check_stock"],
        )

    @override_settings(
        GEMINI_API_KEY="",
        OPENAI_API_KEY="",
        ANTHROPIC_API_KEY="",
        GROQ_API_KEY="",
    )
    def test_fallback_asks_for_part_instead_of_guessing(self):
        Product.objects.create(
            sku="CHN-1001",
            name="Chain Sprocket Kit - Bajaj Pulsar 150",
            category="Chains",
            vehicle_fitment="Bajaj Pulsar 150",
            price=Decimal("2990.00"),
            stock=10,
        )

        response = self.client.post(
            "/api/assistant/chat/",
            data={"session_id": str(uuid4()), "message": "What do you have for Pulsar 150?"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["cited_skus"], [])
        self.assertEqual(body["tool_calls"], [])
        self.assertIn("Which part", body["reply"])

    @override_settings(
        GEMINI_API_KEY="test-key",
        OPENAI_API_KEY="",
        ANTHROPIC_API_KEY="",
        GROQ_API_KEY="",
    )
    @patch("core.assistant.requests.post")
    def test_llm_product_claims_are_replaced_with_database_tool_results(self, post):
        dealer = Dealer.objects.create(
            dealer_code="ASSISTANT-LLM-TEST",
            name="Assistant LLM Test Dealer",
        )
        Product.objects.create(
            sku="BRK-1001",
            name="Brake Shoe Set - Suzuki Gixxer SF",
            category="Brakes",
            vehicle_fitment="Suzuki Gixxer SF",
            price=Decimal("500.00"),
            stock=4,
        )
        Product.objects.create(
            sku="BRK-1044",
            name="Brake Pad Set - Bajaj Pulsar 150",
            category="Brakes",
            vehicle_fitment="Bajaj Pulsar 150",
            price=Decimal("1459.50"),
            stock=136,
        )
        model_responses = [
            {
                "candidates": [{
                    "content": {
                        "parts": [{
                            "functionCall": {
                                "name": "search_parts",
                                "args": {
                                    "query": "brake pads",
                                    "vehicle": "Bajaj Pulsar 150",
                                },
                            },
                        }],
                    },
                }],
            },
            {
                "candidates": [{
                    "content": {
                        "parts": [{
                            "functionCall": {
                                "name": "check_stock",
                                "args": {"skus": ["BRK-1044"]},
                            },
                        }],
                    },
                }],
            },
            {
                "candidates": [{
                    "content": {
                        "parts": [{
                            "text": "BRK-1001 costs ₹1.00 and has 999 in stock.",
                        }],
                    },
                }],
            },
        ]
        post.side_effect = [
            Mock(status_code=200, json=Mock(return_value=result))
            for result in model_responses
        ]

        response = self.client.post(
            "/api/assistant/chat/",
            data={
                "session_id": str(uuid4()),
                "dealer_id": dealer.pk,
                "message": "Do you have brake pads for a Pulsar 150?",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["cited_skus"], ["BRK-1044"])
        self.assertIn("₹1459.50", body["reply"])
        self.assertIn("available stock 136", body["reply"])
        self.assertNotIn("999", body["reply"])
        self.assertNotIn("BRK-1001", body["reply"])
        self.assertEqual(
            [call["name"] for call in body["tool_calls"]],
            ["search_parts", "check_stock"],
        )

    @override_settings(
        GEMINI_API_KEY="",
        OPENAI_API_KEY="",
        ANTHROPIC_API_KEY="",
        GROQ_API_KEY="",
    )
    def test_fallback_does_not_recommend_universal_item_for_vehicle_only_query(self):
        Product.objects.create(
            sku="ACC-5006",
            name="Supplier Special Part 7 - Universal",
            category="Universal Accessories",
            vehicle_fitment="Universal",
            price=Decimal("2229.00"),
            stock=0,
        )

        response = self.client.post(
            "/api/assistant/chat/",
            data={
                "session_id": str(uuid4()),
                "message": "What parts do you have for Pulsar 150?",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["cited_skus"], [])
        self.assertIn("Which part", body["reply"])

    @override_settings(
        GEMINI_API_KEY="",
        OPENAI_API_KEY="",
        ANTHROPIC_API_KEY="",
        GROQ_API_KEY="",
    )
    def test_fallback_follow_up_uses_last_recommended_product(self):
        dealer = Dealer.objects.create(
            dealer_code="ASSISTANT-FOLLOWUP",
            name="Assistant Follow-up Test Dealer",
        )
        Product.objects.create(
            sku="BRK-1044",
            name="Brake Pad Set - Bajaj Pulsar 150",
            category="Brakes",
            vehicle_fitment="Bajaj Pulsar 150",
            price=Decimal("1459.50"),
            stock=136,
        )
        session_id = str(uuid4())

        first = self.client.post(
            "/api/assistant/chat/",
            data={
                "session_id": session_id,
                "dealer_id": dealer.pk,
                "message": "Do you have brake pads for a Pulsar 150?",
            },
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["cited_skus"], ["BRK-1044"])

        follow_up = self.client.post(
            "/api/assistant/chat/",
            data={
                "session_id": session_id,
                "dealer_id": dealer.pk,
                "message": "What is the price?",
            },
            content_type="application/json",
        )

        self.assertEqual(follow_up.status_code, 200)
        body = follow_up.json()
        self.assertEqual(body["cited_skus"], ["BRK-1044"])
        self.assertIn("₹1459.50", body["reply"])
        self.assertIn("available stock is 136", body["reply"])


class OrderContractAndConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.dealer = Dealer.objects.create(
            dealer_code="CONCURRENCY-DEALER",
            name="Concurrency Test Dealer",
        )
        self.product = Product.objects.create(
            sku="CONCURRENCY-001",
            name="Concurrency Test Product",
            price=Decimal("0.03"),
            stock=5,
        )

    def _create_order(self, key=None, quantity=1):
        close_old_connections()
        try:
            return create_order(
                {"dealer_id": self.dealer.pk, "items": [{"sku": self.product.sku, "quantity": quantity}]},
                key,
            )
        finally:
            close_old_connections()

    def test_invalid_pagination_and_malformed_items_use_contract_error_shape(self):
        bad_page = self.client.get("/api/products/?page=abc")
        self.assertEqual(bad_page.status_code, 400)
        self.assertEqual(bad_page.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertIsInstance(bad_page.json()["error"]["details"], list)

        bad_order = self.client.post(
            "/api/orders/",
            data={"dealer_id": self.dealer.pk, "items": [None]},
            content_type="application/json",
        )
        self.assertEqual(bad_order.status_code, 400)
        self.assertEqual(bad_order.json()["error"]["code"], "VALIDATION_ERROR")

        invalid_price = self.client.patch(
            f"/api/products/{self.product.sku}/",
            data={"price": "-1.00"},
            content_type="application/json",
        )
        self.assertEqual(invalid_price.status_code, 400)
        self.assertEqual(invalid_price.json()["error"]["code"], "VALIDATION_ERROR")

    def test_idempotency_key_with_different_body_returns_conflict(self):
        first = self.client.post(
            "/api/orders/",
            data={"dealer_id": self.dealer.pk, "items": [{"sku": self.product.sku, "quantity": 1}]},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="same-key",
        )
        self.assertEqual(first.status_code, 201)
        second = self.client.post(
            "/api/orders/",
            data={"dealer_id": self.dealer.pk, "items": [{"sku": self.product.sku, "quantity": 2}]},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="same-key",
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual(Order.objects.count(), 1)

    def test_draft_tax_rounds_half_up_to_the_paisa(self):
        order, error, duplicate = self._create_order()
        self.assertIsNone(error)
        self.assertFalse(duplicate)
        item = order.items.get()
        self.assertEqual(item.line_subtotal, Decimal("0.03"))
        self.assertEqual(item.line_tax, Decimal("0.01"))
        self.assertEqual(order.grand_total, Decimal("0.04"))

    def test_simultaneous_same_idempotency_key_creates_one_order(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self._create_order("same-request-key"), range(6)))

        self.assertTrue(all(error is None for _, error, _ in results))
        self.assertEqual({order.order_number for order, _, _ in results}, {results[0][0].order_number})
        self.assertEqual(sum(1 for _, _, duplicate in results if duplicate), 5)
        self.assertEqual(Order.objects.count(), 1)

    def test_simultaneous_order_creation_allocates_unique_numbers(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self._create_order(), range(6)))

        self.assertTrue(all(error is None for _, error, _ in results))
        order_numbers = [order.order_number for order, _, _ in results]
        self.assertEqual(len(set(order_numbers)), len(order_numbers))
        self.assertEqual(Order.objects.count(), 6)

    def test_simultaneous_confirmations_do_not_oversell(self):
        orders = [self._create_order(quantity=4)[0] for _ in range(2)]

        def confirm(order_number):
            close_old_connections()
            try:
                request = APIRequestFactory().post("/confirm/", {}, format="json")
                return order_action(request, no=order_number, action="confirm").status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(confirm, [order.order_number for order in orders]))

        self.assertEqual(sorted(statuses), [200, 409])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 1)


class AssistantSafetyTests(TransactionTestCase):
    def setUp(self):
        self.product = Product.objects.create(
            sku="BRK-1044",
            name="Brake Pad Set - KTM Duke 390",
            category="Brakes",
            brand="Test Brand",
            vehicle_fitment="KTM Duke 390",
            price="1234.50",
            stock=7,
        )

    @override_settings(GEMINI_API_KEY="test-key")
    @patch("core.assistant.requests.post")
    def test_llm_text_without_database_tools_cannot_claim_products_prices_or_stock(self, post):
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={"candidates": [{"content": {"parts": [{"text": "BRK-9999 costs ₹1.00 and has 999 in stock."}]}}]}),
        )
        response = self.client.post(
            "/api/assistant/chat/",
            data={"session_id": str(uuid4()), "message": "Can you help me?"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["cited_skus"], [])
        self.assertNotIn("999", body["reply"])
        self.assertNotIn("₹1.00", body["reply"])
        self.assertEqual(body["tool_calls"], [])
        self.assertEqual(post.call_args.kwargs["headers"], {"x-goog-api-key": "test-key"})

    @override_settings(GEMINI_API_KEY="test-key")
    @patch(
        "core.assistant.requests.post",
        side_effect=requests.ConnectionError("provider unavailable"),
    )
    def test_provider_failure_uses_grounded_local_catalogue_fallback(self, post):
        response = self.client.post(
            "/api/assistant/chat/",
            data={"session_id": str(uuid4()), "message": "Check stock for BRK-1044"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("BRK-1044", body["reply"])
        self.assertIn("1234.50", body["reply"])
        self.assertIn("7", body["reply"])
        self.assertEqual(body["cited_skus"], ["BRK-1044"])
        self.assertEqual(body["assistant_mode"], "local_fallback")
        self.assertIn("Gemini is temporarily unavailable", body["provider_notice"])

    @override_settings(GEMINI_API_KEY="test-key")
    @patch("core.assistant.requests.post")
    def test_invalid_gemini_credentials_show_configuration_notice(self, post):
        post.return_value = Mock(
            status_code=403,
            raise_for_status=Mock(
                side_effect=requests.HTTPError(
                    response=Mock(status_code=403)
                )
            ),
        )
        response = self.client.post(
            "/api/assistant/chat/",
            data={"session_id": str(uuid4()), "message": "Check stock for BRK-1044"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["assistant_mode"], "local_fallback")
        self.assertIn("GEMINI_API_KEY", body["provider_notice"])
        self.assertIn("GEMINI_MODEL", body["provider_notice"])

    @override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="gemini-3-flash-preview")
    @patch("core.assistant.requests.post")
    def test_missing_gemini_model_is_reported_as_model_configuration_error(self, post):
        post.return_value = Mock(
            status_code=404,
            raise_for_status=Mock(
                side_effect=requests.HTTPError(
                    response=Mock(status_code=404)
                )
            ),
        )
        response = self.client.post(
            "/api/assistant/chat/",
            data={"session_id": str(uuid4()), "message": "Check stock for BRK-1044"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["assistant_mode"], "local_fallback")
        self.assertIn("can't access the configured model", body["provider_notice"])
        self.assertIn("Gemini API is enabled", body["provider_notice"])
        self.assertTrue(
            post.call_args.args[0].endswith(
                "/models/gemini-3-flash-preview:generateContent"
            )
        )

    @patch("core.views_extra.run_assistant", side_effect=RuntimeError("unexpected failure"))
    def test_unexpected_assistant_failure_is_reported_as_server_error(self, run_assistant):
        response = self.client.post(
            "/api/assistant/chat/",
            data={"session_id": str(uuid4()), "message": "Check stock for BRK-1044"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "ASSISTANT_ERROR")

    @override_settings(GEMINI_API_KEY="test-key")
    @patch("core.assistant.time.sleep")
    @patch("core.assistant.requests.post")
    def test_provider_503_retries_then_uses_grounded_fallback(self, post, sleep):
        import requests
        post.return_value = Mock(
            status_code=503,
            raise_for_status=Mock(side_effect=requests.HTTPError("503")),
        )
        response = self.client.post(
            "/api/assistant/chat/",
            data={"session_id": str(uuid4()), "message": "Check stock for BRK-1044"},
            content_type="application/json",
        )
        body = response.json()
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(body["assistant_mode"], "local_fallback")
        self.assertEqual(body["cited_skus"], ["BRK-1044"])

    @override_settings(GEMINI_API_KEY="")
    def test_partial_make_name_asks_for_specific_vehicle_model(self):
        Product.objects.create(
            sku="MIR-1001", name="Side Mirror Set", category="Mirrors",
            vehicle_fitment="Royal Enfield Classic 350", price="500.00", stock=4,
        )
        Product.objects.create(
            sku="MIR-1002", name="Side Mirror Set", category="Mirrors",
            vehicle_fitment="Royal Enfield Hunter 350", price="600.00", stock=5,
        )
        response = self.client.post(
            "/api/assistant/chat/",
            data={"session_id": str(uuid4()), "message": "royal enfields side mirrors"},
            content_type="application/json",
        )
        body = response.json()
        self.assertIn("multiple vehicles", body["reply"])
        self.assertEqual(body["cited_skus"], [])


class ForecastContractTests(TransactionTestCase):
    def _write_csv(self, path, fields, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_reorder_uses_exact_formula_and_current_database_stock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_csv(
                root / "ml" / "forecast.csv",
                ["sku", "week_start", "forecast_units"],
                [
                    {"sku": "TEST-FORECAST", "week_start": week, "forecast_units": value}
                    for week, value in zip(
                        ("2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19"),
                        ("10.0", "15.0", "20.0", "25.0"),
                    )
                ],
            )
            self._write_csv(
                root / "data" / "lead_times.csv",
                ["sku", "lead_time_weeks"],
                [{"sku": "TEST-FORECAST", "lead_time_weeks": 2}],
            )
            self._write_csv(
                root / "data" / "sales_history.csv",
                ["week_start", "sku", "units_sold", "promo_flag"],
                [
                    {"week_start": week, "sku": "TEST-FORECAST", "units_sold": value, "promo_flag": 0}
                    for week, value in zip(("2026-09-07", "2026-09-14", "2026-09-21"), (1, 3, 5))
                ],
            )
            product = Product.objects.create(
                sku="TEST-FORECAST",
                name="Forecast Test Product",
                price=Decimal("100.00"),
                stock=4,
            )
            with override_settings(BASE_DIR=root, DATA_DIR=root / "data"):
                response = self.client.get("/api/reorder-suggestions/")

        self.assertEqual(response.status_code, 200)
        result = response.json()["results"][0]
        self.assertEqual(result["sku"], product.sku)
        self.assertEqual(result["current_stock"], 4)
        self.assertEqual(result["lead_time_demand"], 25.0)
        self.assertEqual(result["safety_stock"], 5)
        self.assertEqual(result["reorder_point"], 30.0)
        self.assertEqual(result["reorder_qty"], 26)

    def test_invalid_forecast_is_reported_without_server_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_csv(
                root / "ml" / "forecast.csv",
                ["sku", "week_start", "forecast_units"],
                [{"sku": "TEST-FORECAST", "week_start": "2026-09-28", "forecast_units": "-1"}],
            )
            self._write_csv(
                root / "data" / "lead_times.csv",
                ["sku", "lead_time_weeks"],
                [{"sku": "TEST-FORECAST", "lead_time_weeks": 2}],
            )
            with override_settings(BASE_DIR=root, DATA_DIR=root / "data"):
                response = self.client.get("/api/reorder-suggestions/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "FORECAST_UNAVAILABLE")


class SupplierSyncTests(TransactionTestCase):
    supplier_url = "http://supplier.test"
    supplier_key = "supplier-test-key"

    def setUp(self):
        self.existing = Product.objects.create(
            sku="SYNC-STOCK-KEEP",
            name="Old supplier name",
            category="Parts",
            price=Decimal("10.00"),
            stock=7,
        )

    def _mock_get(self, mock):
        def get(url, params, headers, timeout):
            query = urlencode(params)
            path = urlsplit(url).path + (f"?{query}" if query else "")
            status_code, body, response_headers = mock.handle(path, None, headers)
            return Mock(
                status_code=status_code,
                headers=response_headers,
                json=Mock(return_value=body),
            )
        return get

    @override_settings(SUPPLIER_API_URL=supplier_url, SUPPLIER_API_KEY=supplier_key)
    @patch("core.views.time.sleep")
    @patch("core.views.requests.get")
    def test_supplier_mock_pagination_duplicates_stock_and_repeat_sync(self, get, sleep):
        feed = build_public_feed("data/catalogue.csv")
        feed.append({
            "supplier_sku": "SUP-STOCK-KEEP",
            "sku": "SYNC-STOCK-KEEP",
            "name": "Updated supplier name",
            "category": "Parts",
            "brand": "Test Brand",
            "vehicle_fitment": "Universal",
            "description": "Supplier feed item",
            "price": "12.00",
            "supplier_stock": 999,
            "updated_at": "2026-10-12T00:00:00Z",
        })
        mock = SupplierMock(feed, self.supplier_key)
        get.side_effect = self._mock_get(mock)

        first = self.client.post("/api/sync/supplier/")
        self.assertEqual(first.status_code, 200)
        first_summary = first.json()
        self.assertGreater(first_summary["pages_fetched"], 1)
        self.assertEqual(first_summary["requests_made"], first_summary["pages_fetched"])
        self.assertTrue(first_summary["rejected"])

        self.existing.refresh_from_db()
        self.assertEqual(self.existing.name, "Updated supplier name")
        self.assertEqual(self.existing.price, Decimal("12.00"))
        self.assertEqual(self.existing.stock, 7)

        second = self.client.post("/api/sync/supplier/")
        self.assertEqual(second.status_code, 200)
        second_summary = second.json()
        self.assertEqual(second_summary["created"], 0)
        self.assertEqual(second_summary["updated"], 0)
        self.assertEqual(
            second_summary["unchanged"],
            first_summary["created"] + first_summary["updated"] + first_summary["unchanged"],
        )

    @override_settings(SUPPLIER_API_URL=supplier_url, SUPPLIER_API_KEY=supplier_key)
    @patch("core.views.time.sleep")
    @patch("core.views.requests.get")
    def test_supplier_mock_retry_after_zero_is_honored(self, get, sleep):
        feed = [
            {
                "supplier_sku": f"SUP-{index}",
                "sku": f"RETRY-{index}",
                "name": f"Retry item {index}",
                "category": "Parts",
                "brand": "Test Brand",
                "vehicle_fitment": "Universal",
                "description": "",
                "price": "10.00",
                "supplier_stock": 50,
                "updated_at": "2026-10-12T00:00:00Z",
            }
            for index in range(25)
        ]
        mock = SupplierMock(feed, self.supplier_key, throttle_every=2, retry_after=0)
        get.side_effect = self._mock_get(mock)

        response = self.client.post("/api/sync/supplier/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["requests_made"], 3)
        self.assertEqual(response.json()["pages_fetched"], 2)
        sleep.assert_called_once_with(0)

    @override_settings(SUPPLIER_API_URL=supplier_url, SUPPLIER_API_KEY=supplier_key)
    @patch("core.views.time.sleep")
    @patch("core.views.requests.get")
    def test_supplier_outage_does_not_partially_apply_an_earlier_page(self, get, sleep):
        feed = [
            {
                "supplier_sku": f"SUP-OUTAGE-{index}",
                "sku": f"OUTAGE-{index}",
                "name": f"Outage item {index}",
                "category": "Parts",
                "brand": "Test Brand",
                "vehicle_fitment": "Universal",
                "description": "",
                "price": "10.00",
                "supplier_stock": 50,
                "updated_at": "2026-10-12T00:00:00Z",
            }
            for index in range(21)
        ]
        mock = SupplierMock(feed, self.supplier_key, outage_pages={1})
        get.side_effect = self._mock_get(mock)

        response = self.client.post("/api/sync/supplier/")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "SUPPLIER_UNAVAILABLE")
        self.assertEqual(len(mock.log), 6)
        self.assertEqual(Product.objects.filter(sku__startswith="OUTAGE-").count(), 0)
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.stock, 7)
