"""Assemble specialist tools, shared results, and the sales manager."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from agents import Agent, function_tool

from listing_agent import build_listing_agent
from manager_agent import build_manager_agent
from market_agent import build_market_agent
from pricing_agent import build_pricing_agent
from product_agent import build_product_agent
from review_agent import build_review_agent
from woocommerce_client import build_payload, create_product

if TYPE_CHECKING:
    from main import WorkflowState


def build_team(state: WorkflowState, model: Any, *, enable_web: bool = False) -> Agent:
    """Create a fresh team; the manager chooses which tools to call."""
    stage_order = ["facts", "market", "pricing", "copy", "review"]

    def save_specialist_result(stage: str):
        async def extract(result) -> str:
            output = result.final_output
            # Revised results invalidate dependent stages, including earlier approval.
            for downstream in stage_order[stage_order.index(stage) + 1 :]:
                state.results.pop(downstream, None)
            if stage in {"facts", "market"}:
                state.web_lookups.clear()
            state.results[stage] = output
            if stage == "market" and enable_web:
                state.web_lookups = [
                    {
                        "tool": "web_search",
                        "provider": "openai",
                        "sources": [reference.model_dump() for reference in output.references],
                    }
                ]
            if not state.receipt or state.receipt.get("status") == "preview":
                state.receipt = None
                state.payload = None
            state.events.append({"tool": stage, "event": "completed"})
            print(f"  [Specialist completed] {state.stage_names[stage]}", flush=True)
            return output.model_dump_json()

        return extract

    product_agent = build_product_agent(state, model)
    market_agent = build_market_agent(state, model, enable_web=enable_web)
    pricing_agent = build_pricing_agent(state, model)
    listing_agent = build_listing_agent(state, model)
    review_agent = build_review_agent(state, model)

    @function_tool
    async def submit_listing() -> str:
        """Preview the reviewed listing or create a draft or published product."""
        if state.receipt and state.receipt.get("status") != "preview":
            return json.dumps(
                {
                    "message": (
                        "Submission was already attempted in this conversation. "
                        "No duplicate will be created; check the original result."
                    ),
                    "original_receipt": state.receipt,
                },
                ensure_ascii=False,
            )
        missing_stages = [
            state.stage_names[stage] for stage in stage_order if stage not in state.results
        ]
        if missing_stages:
            return (
                "Missing stages: "
                + ", ".join(missing_stages)
                + ". Call the corresponding specialists, then call submit_listing again."
            )
        facts = state.results["facts"]
        pricing = state.results["pricing"]
        listing = state.results["copy"]
        review = state.results["review"]
        if facts.missing_questions:
            return "Essential product information is missing. Ask the seller first."
        if not review.approved or review.issues:
            return "Review has not passed. Revise the listing and review it again."
        if pricing.currency != state.store.currency:
            return (
                "The pricing currency does not match the store configuration. Reprice the product."
            )
        if not pricing.suggested_price:
            return "Ask the seller for a price, or obtain market evidence and reprice first."
        state.payload = build_payload(
            listing,
            pricing,
            quantity=state.quantity,
            status="publish" if state.mode == "publish" else "draft",
        )
        if state.mode == "preview":
            receipt = {
                "status": "preview",
                "message": "Local preview only; the store was not contacted or modified.",
            }
        else:
            # Record the attempt before posting to prevent retries after interruption.
            state.receipt = {
                "status": "unknown",
                "message": (
                    "Submission is in progress or was interrupted. Check the store backend first."
                ),
            }
            receipt = await create_product(state.store, state.payload)
        state.receipt = receipt
        state.events.append({"tool": "submit_listing", "event": receipt["status"]})
        print(f"  [Submission result] {receipt['status']}", flush=True)
        return json.dumps(receipt, ensure_ascii=False)

    tools = [
        product_agent.as_tool(
            tool_name="analyze_product",
            tool_description="Organize product facts and identify essential missing information.",
            custom_output_extractor=save_specialist_result("facts"),
            max_turns=4,
        ),
        market_agent.as_tool(
            tool_name="research_market",
            tool_description=(
                "Research comparable prices after facts are ready. Search and read pages when "
                "web access is enabled; otherwise analyze supplied materials."
            ),
            custom_output_extractor=save_specialist_result("market"),
            max_turns=6,
        ),
        pricing_agent.as_tool(
            tool_name="recommend_price",
            tool_description=(
                "Recommend a price after facts and market research. Without market evidence, "
                "an explicitly specified seller price may be used."
            ),
            custom_output_extractor=save_specialist_result("pricing"),
            max_turns=4,
        ),
        listing_agent.as_tool(
            tool_name="write_listing",
            tool_description=(
                "Write copy after facts and pricing, or revise it using review feedback."
            ),
            custom_output_extractor=save_specialist_result("copy"),
            max_turns=4,
        ),
        review_agent.as_tool(
            tool_name="review_listing",
            tool_description=(
                "Review facts, market, pricing, and copy when all are ready. "
                "Review again after copy changes."
            ),
            custom_output_extractor=save_specialist_result("review"),
            max_turns=4,
        ),
        submit_listing,
    ]
    return build_manager_agent(state, model, tools)
