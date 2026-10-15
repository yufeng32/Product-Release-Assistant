"""Create the OpenAI Responses model used by every agent."""

import os

import httpx
from agents import OpenAIResponsesModel
from openai import AsyncOpenAI

DEFAULT_OPENAI_MODEL = "gpt-5-nano"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


def create_model() -> OpenAIResponsesModel:
    """Create an OpenAI Responses API model from environment configuration."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Set OPENAI_API_KEY in the project's .env file.")

    client_options = {
        "api_key": api_key,
        "base_url": os.getenv("OPENAI_BASE_URL", "").strip() or DEFAULT_OPENAI_BASE_URL,
        "timeout": httpx.Timeout(600, connect=30),
    }

    return OpenAIResponsesModel(
        model=os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL,
        openai_client=AsyncOpenAI(**client_options),
    )
