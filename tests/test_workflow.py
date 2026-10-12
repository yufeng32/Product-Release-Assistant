"""Offline tests: real SDK orchestration with scripted model replies, plus mocked HTTP."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from agents import Model, ModelResponse, RunConfig, Runner, Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from agent_team import build_team
from main import WorkflowState
from models import ListingCopy, PricingAdvice
from woocommerce_client import StoreConfig, build_payload, create_product

FIXTURES = {
    "ProductFacts": {
        "product_name": "Test monitor",
        "confirmed_details": ["Model X", "Working normally"],
        "defects": ["Case scratches"],
        "selling_goal": "Sell the monitor",
        "missing_questions": [],
    },
    "MarketReport": {
        "references": [],
        "summary": "No market samples",
        "limitations": ["Using the seller's asking price"],
    },
    "PricingAdvice": {
        "suggested_price": "100.00",
        "currency": "CNY",
        "rationale": "Seller's asking price",
        "negotiation_advice": "The seller decides",
        "confidence": "low",
    },
    "ListingCopy": {
        "title": "Test monitor",
        "short_description": "Used",
        "description": "Working normally, with case scratches.",
        "photo_suggestions": ["Photograph the scratches"],
    },
    "ReviewReport": {"approved": True, "issues": [], "suggested_changes": []},
    "ManagerAnswer": {"message": "The test is complete", "questions": []},
}
NORMAL_CALLS = [
    "analyze_product",
    "research_market",
    "recommend_price",
    "write_listing",
    "review_listing",
    "submit_listing",
]


class ScriptedModel(Model):
    """No network: replaces only model generation; SDK tool dispatch still runs normally."""

    def __init__(self, calls, *, fail_first_review=False):
        self.calls = list(calls)
        self.fail_first_review = fail_first_review
        self.review_count = 0
        self.manager_turns = 0
        self.seen_schemas = []
        self.seen_contexts = []

    async def get_response(self, **kwargs):
        schema = kwargs["output_schema"].name()
        self.seen_schemas.append(schema)
        self.seen_contexts.append((schema, kwargs["system_instructions"]))
        if schema == "ManagerAnswer" and self.manager_turns < len(self.calls):
            name = self.calls[self.manager_turns]
            self.manager_turns += 1
            assert name in [tool.name for tool in kwargs["tools"]], name
            output = ResponseFunctionToolCall(
                type="function_call",
                call_id=f"call_{self.manager_turns}",
                name=name,
                arguments=json.dumps(
                    {} if name == "submit_listing" else {"input": "Perform the assigned task"}
                ),
            )
        else:
            data = dict(FIXTURES[schema])
            if schema == "ReviewReport":
                self.review_count += 1
                if self.fail_first_review and self.review_count == 1:
                    data = {
                        "approved": False,
                        "issues": ["The defects need clarification"],
                        "suggested_changes": ["Add an explanation"],
                    }
            output = ResponseOutputMessage(
                id=f"msg_{len(self.seen_schemas)}",
                type="message",
                role="assistant",
                status="completed",
                content=[
                    ResponseOutputText(type="output_text", text=json.dumps(data), annotations=[])
                ],
            )
        return ModelResponse(output=[output], usage=Usage(), response_id=None)

    async def stream_response(self, **kwargs):
        raise NotImplementedError("Streaming is not used in these tests")
        yield  # Makes this method an async iterator as required by the SDK interface.


class OrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, calls, *, mode="preview", fail_first_review=False):
        state = WorkflowState(
            request="Test product, with a seller asking price of 100 CNY",
            store=StoreConfig(),
            mode=mode,
        )
        model = ScriptedModel(calls, fail_first_review=fail_first_review)
        team = build_team(state, model=model, enable_web=False)
        result = await Runner.run(
            team, state.request, max_turns=24, run_config=RunConfig(tracing_disabled=True)
        )
        return state, model, result

    async def test_real_sdk_calls_all_five_specialists_and_preview(self):
        with patch("agent_team.create_product", new_callable=AsyncMock) as post:
            state, model, result = await self.execute(NORMAL_CALLS)
        self.assertEqual(set(state.results), {"facts", "market", "pricing", "copy", "review"})
        self.assertEqual(state.receipt["status"], "preview")
        self.assertEqual(state.payload["regular_price"], "100.00")
        self.assertEqual(result.last_agent.name, "Sales Manager")
        self.assertIn("ReviewReport", model.seen_schemas)
        post.assert_not_awaited()

    async def test_review_failure_can_trigger_revision(self):
        calls = NORMAL_CALLS[:-1] + [
            "submit_listing",
            "write_listing",
            "review_listing",
            "submit_listing",
        ]
        state, model, _ = await self.execute(calls, fail_first_review=True)
        self.assertEqual(model.review_count, 2)
        self.assertEqual(
            [e["event"] for e in state.events if e["tool"] == "submit_listing"], ["preview"]
        )

    async def test_editing_copy_invalidates_previous_review(self):
        state, _, _ = await self.execute(NORMAL_CALLS[:-1] + ["write_listing", "submit_listing"])
        self.assertNotIn("review", state.results)
        self.assertIsNone(state.receipt)

    async def test_repricing_invalidates_copy_and_approval_before_submission(self):
        calls = NORMAL_CALLS + ["recommend_price", "submit_listing"]
        with patch("agent_team.create_product", new_callable=AsyncMock) as post:
            state, _, _ = await self.execute(calls)
        self.assertEqual(set(state.results), {"facts", "market", "pricing"})
        self.assertIsNone(state.receipt)
        self.assertIsNone(state.payload)
        post.assert_not_awaited()

    async def test_teams_have_independent_live_shared_context(self):
        states = [
            WorkflowState(request="First seller's monitor", store=StoreConfig()),
            WorkflowState(request="Second seller's keyboard", store=StoreConfig()),
        ]
        models = [ScriptedModel(NORMAL_CALLS), ScriptedModel(NORMAL_CALLS)]
        teams = [
            build_team(state, model=model) for state, model in zip(states, models, strict=True)
        ]
        await asyncio.gather(
            *(
                Runner.run(
                    team, state.request, max_turns=24, run_config=RunConfig(tracing_disabled=True)
                )
                for team, state in zip(teams, states, strict=True)
            )
        )

        self.assertIsNot(teams[0], teams[1])
        self.assertIsNot(states[0].results, states[1].results)
        for index, (state, model) in enumerate(zip(states, models, strict=True)):
            contexts = [
                (schema, json.loads(instructions.rsplit("\n", 1)[-1]))
                for schema, instructions in model.seen_contexts
            ]
            self.assertTrue(
                all(context["user_request"] == state.request for _, context in contexts)
            )
            self.assertTrue(
                all(
                    states[1 - index].request not in instructions
                    for _, instructions in model.seen_contexts
                )
            )
            product_context = next(
                context for schema, context in contexts if schema == "ProductFacts"
            )
            market_context = next(
                context for schema, context in contexts if schema == "MarketReport"
            )
            review_context = next(
                context for schema, context in contexts if schema == "ReviewReport"
            )
            self.assertEqual(product_context["current_results"], {})
            self.assertEqual(market_context["current_results"]["facts"], FIXTURES["ProductFacts"])
            self.assertEqual(
                set(review_context["current_results"]), {"facts", "market", "pricing", "copy"}
            )

    async def test_missing_facts_and_wrong_currency_block_http_submission(self):
        blocked_results = [
            {
                "ProductFacts": dict(
                    FIXTURES["ProductFacts"], missing_questions=["Does the monitor work?"]
                )
            },
            {"PricingAdvice": dict(FIXTURES["PricingAdvice"], currency="USD")},
        ]
        for fixtures in blocked_results:
            with self.subTest(stage=next(iter(fixtures))):
                with (
                    patch.dict(FIXTURES, fixtures),
                    patch("agent_team.create_product", new_callable=AsyncMock) as post,
                ):
                    state, _, _ = await self.execute(NORMAL_CALLS, mode="publish")
                self.assertIsNone(state.receipt)
                self.assertIsNone(state.payload)
                post.assert_not_awaited()

    async def test_submit_before_analysis_does_not_post(self):
        with patch("agent_team.create_product", new_callable=AsyncMock) as post:
            state, _, _ = await self.execute(["submit_listing"], mode="publish")
        self.assertIsNone(state.receipt)
        post.assert_not_awaited()

    async def test_repeated_submit_does_not_create_two_products(self):
        with patch(
            "agent_team.create_product",
            new_callable=AsyncMock,
            return_value={"status": "publish", "product_id": 42},
        ) as post:
            state, _, _ = await self.execute(NORMAL_CALLS + ["submit_listing"], mode="publish")
        post.assert_awaited_once()
        self.assertEqual(state.receipt["product_id"], 42)

    async def test_missing_prices_cannot_be_submitted(self):
        for price in [None, ""]:
            with self.subTest(price=price):
                pricing = dict(FIXTURES["PricingAdvice"], suggested_price=price)
                with (
                    patch.dict(FIXTURES, {"PricingAdvice": pricing}),
                    patch("agent_team.create_product", new_callable=AsyncMock) as post,
                ):
                    state, _, _ = await self.execute(NORMAL_CALLS, mode="publish")
                self.assertIsNone(state.receipt)
                post.assert_not_awaited()

    async def test_interrupted_submit_keeps_receipt_and_does_not_retry(self):
        with patch(
            "agent_team.create_product",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("Submission timed out"),
        ) as post:
            state, _, _ = await self.execute(NORMAL_CALLS + ["submit_listing"], mode="publish")
        self.assertEqual(state.receipt["status"], "unknown")
        post.assert_awaited_once()

    async def test_export_does_not_contain_store_secrets(self):
        state = WorkflowState(
            request="Product",
            store=StoreConfig(consumer_key="SECRET_KEY", consumer_secret="SECRET_VALUE"),
        )
        self.assertNotIn("SECRET", state.public_context())
        self.assertNotIn("SECRET", json.dumps(state.export()))


class StoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = StoreConfig(
            url="https://store.example", consumer_key="ck_test", consumer_secret="cs_test"
        )
        self.copy = ListingCopy.model_validate(FIXTURES["ListingCopy"])
        self.pricing = PricingAdvice.model_validate(FIXTURES["PricingAdvice"])

    def payload(self, **kwargs):
        return build_payload(
            self.copy, self.pricing, quantity=1, image_urls=[], status="draft", **kwargs
        )

    def test_plain_text_is_escaped_and_media_ids_preserved(self):
        self.copy.description = "<script>alert(1)</script>\nCase scratches"
        data = self.payload(image_ids=[5])
        self.assertNotIn("<script>", data["description"])
        self.assertEqual(data["images"], [{"id": 5}])
        self.assertEqual(data["stock_quantity"], 1)

    async def test_create_posts_product_and_returns_receipt(self):
        requests = []

        def handler(request):
            requests.append(request)
            self.assertEqual(request.method, "POST")
            self.assertEqual(json.loads(request.content)["status"], "draft")
            self.assertEqual(json.loads(request.content)["regular_price"], "100.00")
            self.assertTrue(request.headers["Authorization"].startswith("Basic "))
            return httpx.Response(
                201, json={"id": 42, "status": "draft", "permalink": "https://store.example/?p=42"}
            )

        result = await create_product(
            self.config, self.payload(), transport=httpx.MockTransport(handler)
        )
        self.assertEqual(result["product_id"], 42)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[-1].url.path, "/wp-json/wc/v3/products")

    async def test_timeout_raises_and_is_not_retried(self):
        methods = []

        def handler(request):
            methods.append(request.method)
            raise httpx.ReadTimeout("Timeout", request=request)

        with self.assertRaises(httpx.ReadTimeout):
            await create_product(
                self.config, self.payload(), transport=httpx.MockTransport(handler)
            )
        self.assertEqual(methods, ["POST"])

    async def test_http_errors_raise(self):
        for status in [401, 500]:
            with self.subTest(status=status):
                transport = httpx.MockTransport(
                    lambda request, response_status=status: httpx.Response(response_status)
                )
                with self.assertRaises(httpx.HTTPStatusError) as caught:
                    await create_product(self.config, self.payload(), transport=transport)
                self.assertEqual(caught.exception.response.status_code, status)


if __name__ == "__main__":
    unittest.main()
