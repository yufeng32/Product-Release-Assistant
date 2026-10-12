"""Review the current listing from a buyer's perspective."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agents import Agent

from agent_instructions import render_instructions
from models import ReviewReport

if TYPE_CHECKING:
    from main import WorkflowState

INSTRUCTIONS = """
Check current facts, market, pricing, and copy for omitted defects, invented
selling points, contradictions, unsupported pricing claims, or buyer confusion.
Provide actionable feedback for each problem.
Return approved=false while required fixes remain. Otherwise, return
approved=true and issues=[].
Keep optional improvements such as photo_suggestions in suggested_changes, not
issues, and do not require seller input for these improvements before proceeding.
An evidence-based suggested price may be used for preview or submission without
requiring the seller to provide a price first. Confirmed product facts do not
need another authorization for disclosure.
Do not describe this review as a guarantee of legal compliance or physical
product inspection.
"""


def build_review_agent(state: WorkflowState, model: Any) -> Agent:
    """Create the buyer perspective reviewer for this conversation."""
    return Agent(
        name="Buyer Perspective Reviewer",
        instructions=render_instructions(state, INSTRUCTIONS),
        model=model,
        output_type=ReviewReport,
    )
