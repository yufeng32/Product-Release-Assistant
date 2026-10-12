"""Recommend a price using product facts, market evidence, and seller goals."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agents import Agent

from agent_instructions import render_instructions
from models import PricingAdvice

if TYPE_CHECKING:
    from main import WorkflowState

INSTRUCTIONS = """
Use facts and market evidence, desired selling speed, accessories, and defects to
recommend a price. The currency must match the shared state.
Explain differences between samples and provide negotiation advice. Asking
prices do not guarantee a sale, and you cannot promise a sale by a deadline.
When comparable evidence exists, analyze actual prices, condition, and the
selling goal instead of merely repeating the seller's price. Compare the seller's
expected price with the evidence when appropriate.
If the user requests automatic analysis and pricing and usable evidence exists,
the suggested price may be used for the current preview or submission without
asking for extra confirmation just because the seller did not specify a price.
Respect an explicitly fixed asking price and explain differences from market
evidence without changing the price yourself.
If no usable market evidence exists, use an explicitly provided asking price and
explain the limitation. If neither exists, return suggested_price=null.
Use a decimal string without a currency symbol and at most two decimal places.
Do not automatically convert foreign currencies. Pricing based on demonstration
samples must say in rationale that it is for demonstration only, not real market
evidence.
"""


def build_pricing_agent(state: WorkflowState, model: Any) -> Agent:
    """Create the pricing strategist for this conversation."""
    return Agent(
        name="Pricing Strategist",
        instructions=render_instructions(state, INSTRUCTIONS),
        model=model,
        output_type=PricingAdvice,
    )
