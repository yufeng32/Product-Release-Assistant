"""Analyze confirmed product facts and identify essential missing information."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agents import Agent

from agent_instructions import render_instructions
from models import ProductFacts

if TYPE_CHECKING:
    from main import WorkflowState

INSTRUCTIONS = """
Organize the product model, specifications, condition, defects, accessories, and
selling goal. Include missing_questions only when essential missing information
affects pricing or the accuracy of the listing.
Do not require every product to have a warranty or invoice, and do not treat
unprovided facts as known. If the seller has not specified a price, let market
research and pricing suggest one; do not ask for an asking price or minimum here.
Photos are optional improvements, so exclude them from missing_questions. Record
defects, missing packaging, or lack of warranty already confirmed by the seller
without asking for permission to disclose them again.
For a course demonstration draft with a confirmed product type, functionality,
defects, accessories, and an explicitly specified asking price, brand/model,
dimensions/color, and photos may remain unspecified. Do not ask missing_questions
solely for these details, and do not present unknown details as facts.
"""


def build_product_agent(state: WorkflowState, model: Any) -> Agent:
    """Create the product analyst for this conversation."""
    return Agent(
        name="Product Analyst",
        instructions=render_instructions(state, INSTRUCTIONS),
        model=model,
        output_type=ProductFacts,
    )
