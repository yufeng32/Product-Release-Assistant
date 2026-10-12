"""Adapt DeepSeek JSON responses to the OpenAI Agents SDK."""

import json
import os
from dataclasses import replace

from agents import FunctionTool, OpenAIChatCompletionsModel
from openai import AsyncOpenAI


class DeepSeekModel(OpenAIChatCompletionsModel):
    """Request JSON objects matching the SDK's structured output schema."""

    async def get_response(
        self,
        *,
        system_instructions,
        model_settings,
        output_schema,
        tools,
        **kwargs,
    ):
        extra_body = dict(model_settings.extra_body or {})
        extra_body["thinking"] = {"type": "disabled"}
        # DeepSeek uses json_object instead of the SDK's default json_schema.
        if output_schema is not None:
            system_instructions = (system_instructions or "") + (
                "\nCall tools normally when needed. The final response must be a JSON object "
                "matching the following JSON Schema. Do not use Markdown code fences."
                "\nJSON Schema:\n" + json.dumps(output_schema.json_schema(), ensure_ascii=False)
            )
            extra_body["response_format"] = {"type": "json_object"}
        tools = [
            replace(tool, strict_json_schema=False) if isinstance(tool, FunctionTool) else tool
            for tool in tools
        ]
        return await super().get_response(
            system_instructions=system_instructions,
            model_settings=replace(model_settings, extra_body=extra_body),
            output_schema=None,
            tools=tools,
            **kwargs,
        )


def create_model() -> DeepSeekModel:
    """Create a model connection using the environment configuration."""
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Set DEEPSEEK_API_KEY in the project's .env file.")
    return DeepSeekModel(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        openai_client=AsyncOpenAI(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        ),
    )
