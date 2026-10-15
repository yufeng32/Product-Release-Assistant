"""Verify the OpenAI Responses model and hosted web-search configuration."""

import os
import unittest
from unittest.mock import patch

from agents import OpenAIResponsesModel, WebSearchTool

from main import WorkflowState
from market_agent import build_market_agent
from openai_model import DEFAULT_OPENAI_BASE_URL, DEFAULT_OPENAI_MODEL, create_model
from woocommerce_client import StoreConfig


class OpenAIModelTests(unittest.TestCase):
    def setUp(self):
        self.state = WorkflowState(request="Used monitor", store=StoreConfig())

    def test_create_model_uses_openai_responses_api_configuration(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "offline-test-key",
                "OPENAI_BASE_URL": "https://proxy.example/v1",
                "OPENAI_MODEL": "test-model",
            },
            clear=False,
        ):
            model = create_model()

        self.assertIsInstance(model, OpenAIResponsesModel)
        self.assertEqual(model.model, "test-model")
        self.assertEqual(str(model._client.base_url), "https://proxy.example/v1/")
        self.assertEqual(model._client.timeout.connect, 30)

    def test_create_model_requires_openai_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                create_model()

    def test_low_cost_model_is_the_default(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "offline-test-key",
                "OPENAI_BASE_URL": "",
                "OPENAI_MODEL": "",
            },
            clear=False,
        ):
            model = create_model()

        self.assertEqual(DEFAULT_OPENAI_MODEL, "gpt-5-nano")
        self.assertEqual(DEFAULT_OPENAI_BASE_URL, "https://api.openai.com/v1")
        self.assertEqual(model.model, "gpt-5-nano")
        self.assertEqual(str(model._client.base_url), "https://api.openai.com/v1/")

    def test_market_agent_uses_hosted_web_search(self):
        agent = build_market_agent(self.state, "test-model", enable_web=True)

        self.assertEqual(len(agent.tools), 1)
        self.assertIsInstance(agent.tools[0], WebSearchTool)
        self.assertEqual(agent.tools[0].search_context_size, "medium")
        self.assertEqual(agent.model_settings.tool_choice, "required")

    def test_no_web_mode_has_no_hosted_tool(self):
        agent = build_market_agent(self.state, "test-model", enable_web=False)

        self.assertEqual(agent.tools, [])
        self.assertIsNone(agent.model_settings.tool_choice)


if __name__ == "__main__":
    unittest.main()
