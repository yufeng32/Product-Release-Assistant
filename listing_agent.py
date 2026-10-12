"""Write honest product descriptions from confirmed facts and pricing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agents import Agent

from agent_instructions import render_instructions
from models import ListingCopy

if TYPE_CHECKING:
    from main import WorkflowState

INSTRUCTIONS = """
Use facts and pricing to write a product title, short description, and complete
description. Return plain text, not HTML.
Cover selling points, model, actual condition, known defects, accessories, and
transaction terms confirmed by the seller.
If review exists, revise the copy to address its feedback. Do not independently
promise free shipping, new condition, a warranty, or return terms.
Use photo_suggestions for missing product photos. Do not invent images or borrow
competitors' photos.
"""


def build_listing_agent(state: WorkflowState, model: Any) -> Agent:
    """Create the sales copywriter for this conversation."""
    return Agent(
        name="Sales Copywriter",
        instructions=render_instructions(state, INSTRUCTIONS),
        model=model,
        output_type=ListingCopy,
    )
