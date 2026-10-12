"""Research comparable prices using available sources."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from agents import Agent, FunctionTool, ModelSettings

from agent_instructions import render_instructions
from models import MarketReport

if TYPE_CHECKING:
    from main import WorkflowState

INSTRUCTIONS = """
Find comparable products using facts, preferring the same model, specifications,
and similar condition. The current date in Macau is {current_date}.
{search_instructions}
Record each source URL, price, currency, condition, observation time, and
comparability. Label advertised prices as asking; use sold only when the source
explicitly proves a completed sale. Use unknown for missing prices. Do not invent
URLs or live data.
If evidence is insufficient, return empty references and explain limitations.
Do not directly compare amounts in different currencies. Explicitly identify
user-supplied demonstration or fictional samples in both limitations and summary.
Only use prices explicitly shown in search snippets or page text and clearly
associated with the product. Do not fill prices from model memory. Use
price=unknown when the price is missing or its product is unclear.
Explain limitations when a site requires login, hides prices, or blocks access;
finding a link does not establish a price.
Do not treat rent, shipping fees, accessories, or a complete computer's price as
the price of a single product. Distinguish new, used, and refurbished products.
If a page shows only $ without a clear currency, record an unknown currency
instead of assuming CNY. Keep foreign-currency samples as separate references.
"""

WEB_INSTRUCTIONS = """
Call search_web first to find comparable products; the tool searches and reads
page text. Prefer the same model, used condition, and the market matching the
store currency. Search at most twice, changing keywords if the first results are
unsuitable. Compare market prices even when the seller provides an asking price.
"""

OFFLINE_INSTRUCTIONS = """
Web access is disabled. Analyze only references supplied by the seller.
"""


def build_market_agent(
    state: WorkflowState,
    model: Any,
    search_web: FunctionTool,
    *,
    enable_web: bool = False,
) -> Agent:
    """Create the researcher with the conversation's web access setting."""
    instructions = INSTRUCTIONS.format(
        current_date=datetime.now(timezone(timedelta(hours=8))).date(),
        search_instructions=WEB_INSTRUCTIONS if enable_web else OFFLINE_INSTRUCTIONS,
    )
    return Agent(
        name="Market Researcher",
        instructions=render_instructions(state, instructions),
        model=model,
        output_type=MarketReport,
        tools=[search_web] if enable_web else [],
        model_settings=ModelSettings(
            tool_choice="search_web" if enable_web else None,
            parallel_tool_calls=False,
        ),
    )
