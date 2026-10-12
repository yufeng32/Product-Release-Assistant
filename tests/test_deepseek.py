"""Exercise the real SDK and DeepSeek request adapter with offline HTTP responses."""

import json
import unittest
from unittest.mock import patch

import httpx
from agents import Agent, RunConfig, Runner
from agents.exceptions import ModelBehaviorError
from ddgs.exceptions import DDGSException
from openai import AsyncOpenAI
from test_workflow import FIXTURES, NORMAL_CALLS

from agent_team import build_team
from main import DeepSeekModel, WorkflowState
from models import ManagerAnswer, ProductFacts
from woocommerce_client import StoreConfig


def completion(message):
    return httpx.Response(
        200,
        json={
            "id": "offline-completion",
            "object": "chat.completion",
            "created": 0,
            "model": "deepseek-flash",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        },
    )


class DeepSeekTests(unittest.IsolatedAsyncioTestCase):
    async def check_market_search(self, *, unavailable=False, match_count=1):
        url = "https://prices.example/monitor"
        market_turn = 0
        manager_turn = 0
        report = {
            "references": [],
            "summary": "The search returned no prices",
            "limitations": ["Search is temporarily unavailable"],
        }
        if not unavailable:
            report = {
                "references": [
                    {
                        "title": "Used monitor X",
                        "url": url,
                        "price": "120.00",
                        "currency": "CNY",
                        "price_kind": "asking",
                        "condition": "Working normally",
                        "observed_at": "2026-10-10",
                        "comparability": "The same used model",
                    }
                ],
                "summary": "The page lists an asking price of 120 CNY",
                "limitations": ["An asking price is not a sale price"],
            }

        def tool_call(name, arguments):
            return completion(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{name}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
            )

        def handler(request):
            nonlocal market_turn, manager_turn
            body = json.loads(request.content)
            schema = json.loads(body["messages"][0]["content"].split("\nJSON Schema:\n")[-1])
            if schema["title"] == "ManagerAnswer":
                manager_turn += 1
                if manager_turn == 1:
                    return tool_call(
                        "research_market", {"input": "Search online for prices of the same model"}
                    )
                return completion(
                    {"role": "assistant", "content": json.dumps(FIXTURES["ManagerAnswer"])}
                )
            self.assertEqual(schema["title"], "MarketReport")
            self.assertEqual({tool["function"]["name"] for tool in body["tools"]}, {"search_web"})
            self.assertTrue(all(tool["type"] == "function" for tool in body["tools"]))
            market_turn += 1
            if market_turn == 1:
                self.assertEqual(
                    body["tool_choice"], {"type": "function", "function": {"name": "search_web"}}
                )
                return tool_call("search_web", {"query": "monitor X used price"})
            self.assertNotIn(
                body.get("tool_choice"), [{"type": "function", "function": {"name": "search_web"}}]
            )
            return completion({"role": "assistant", "content": json.dumps(report)})

        with patch("agent_team.DDGS") as search_client:
            matches = [
                {
                    "title": f"Used monitor X {index}",
                    "href": url if index == 0 else f"{url}/{index}",
                    "body": "Asking price: 120 CNY",
                }
                for index in range(match_count)
            ]
            search_client.return_value.text.return_value = matches
            search_client.return_value.extract.return_value = {
                "url": url,
                "content": "Used monitor X, working normally, asking price 120.00 CNY.",
            }
            if unavailable:
                search_client.return_value.text.side_effect = DDGSException(
                    "offline search failure"
                )
            async with AsyncOpenAI(
                api_key="offline-test-key",
                base_url="https://api.deepseek.com",
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            ) as client:
                state = WorkflowState(request="Test product", store=StoreConfig())
                state.results["facts"] = ProductFacts.model_validate(FIXTURES["ProductFacts"])
                team = build_team(
                    state,
                    DeepSeekModel(model="deepseek-flash", openai_client=client),
                    enable_web=True,
                )
                await Runner.run(
                    team, state.request, max_turns=24, run_config=RunConfig(tracing_disabled=True)
                )
            search_client.return_value.text.assert_called_once_with(
                "monitor X used price",
                region="cn-zh",
                max_results=6,
            )
            if unavailable:
                search_client.return_value.extract.assert_not_called()
                self.assertEqual(state.results["market"].references, [])
                self.assertIn("message", state.web_lookups[0])
            else:
                read_pages = search_client.return_value.extract.call_args_list
                self.assertEqual(len(read_pages), min(match_count, 3))
                self.assertCountEqual(
                    [(call.args[0], call.kwargs) for call in read_pages],
                    [(match["href"], {"fmt": "text_plain"}) for match in matches[:3]],
                )
                results = state.web_lookups[0]["results"]
                self.assertEqual(
                    [item["url"] for item in results], [match["href"] for match in matches]
                )
                self.assertTrue(all("text" in item for item in results[:3]))
                self.assertTrue(all("text" not in item for item in results[3:]))
                self.assertEqual(state.results["market"].references[0].price, "120.00")
                self.assertEqual([lookup["tool"] for lookup in state.web_lookups], ["search_web"])
                self.assertIn("120.00", state.export()["web_lookups"][0]["results"][0]["text"])
            self.assertIsNone(state.receipt)

    async def test_real_sdk_market_agent_searches_and_reads_page(self):
        await self.check_market_search()

    async def test_search_failure_returns_limitations_without_prices(self):
        await self.check_market_search(unavailable=True)

    async def test_search_preserves_six_hits_and_reads_only_first_three(self):
        await self.check_market_search(match_count=6)

    async def test_full_team_uses_deepseek_chat_and_json_mode(self):
        requests = []
        manager_turn = 0

        def handler(request):
            nonlocal manager_turn
            self.assertEqual(str(request.url), "https://api.deepseek.com/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer offline-test-key")
            body = json.loads(request.content)
            requests.append(body)
            self.assertEqual(body["model"], "deepseek-flash")
            self.assertEqual(body["response_format"], {"type": "json_object"})
            self.assertEqual(body["thinking"], {"type": "disabled"})
            for tool in body.get("tools", []):
                self.assertEqual(tool["type"], "function")
                self.assertFalse(tool["function"]["strict"])
            schema = json.loads(body["messages"][0]["content"].split("\nJSON Schema:\n")[-1])
            name = schema["title"]
            if name == "ManagerAnswer":
                # Real models must see the whole team, including on the first turn.
                self.assertEqual({t["function"]["name"] for t in body["tools"]}, set(NORMAL_CALLS))
            if name == "ManagerAnswer" and manager_turn < len(NORMAL_CALLS):
                tool_name = NORMAL_CALLS[manager_turn]
                manager_turn += 1
                self.assertIn(tool_name, [t["function"]["name"] for t in body["tools"]])
                return completion(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"call_{manager_turn}",
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps(
                                        {}
                                        if tool_name == "submit_listing"
                                        else {"input": "Perform the assigned task"}
                                    ),
                                },
                            }
                        ],
                    }
                )
            return completion({"role": "assistant", "content": json.dumps(FIXTURES[name])})

        async with AsyncOpenAI(
            api_key="offline-test-key",
            base_url="https://api.deepseek.com",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ) as client:
            state = WorkflowState(
                request="Test product, with an asking price of 100 CNY", store=StoreConfig()
            )
            team = build_team(state, DeepSeekModel(model="deepseek-flash", openai_client=client))
            result = await Runner.run(
                team, state.request, max_turns=24, run_config=RunConfig(tracing_disabled=True)
            )
        self.assertIsInstance(result.final_output, ManagerAnswer)
        self.assertEqual(state.receipt["status"], "preview")
        self.assertEqual(len(state.results), 5)
        self.assertEqual(len(requests), 12)
        self.assertTrue(any(m["role"] == "tool" for m in requests[-1]["messages"]))

    async def test_sdk_still_rejects_wrong_json_fields(self):
        transport = httpx.MockTransport(
            lambda request: completion(
                {
                    "role": "assistant",
                    "content": '{"wrong_field": true}',
                }
            )
        )
        async with AsyncOpenAI(
            api_key="offline-test-key",
            base_url="https://api.deepseek.com",
            http_client=httpx.AsyncClient(transport=transport),
        ) as client:
            agent = Agent(
                name="Test",
                model=DeepSeekModel(model="deepseek-flash", openai_client=client),
                output_type=ManagerAnswer,
            )
            with self.assertRaises(ModelBehaviorError):
                await Runner.run(agent, "Test", run_config=RunConfig(tracing_disabled=True))
