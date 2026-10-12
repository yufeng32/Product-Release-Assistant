"""Keep workflow state, read seller inputs, run the manager, and save results."""

import argparse
import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from agents import RunConfig, Runner
from dotenv import load_dotenv
from pydantic import BaseModel

from agent_team import build_team
from deepseek_model import DeepSeekModel as DeepSeekModel
from deepseek_model import create_model
from models import ManagerAnswer
from woocommerce_client import StoreConfig

ROOT = Path(__file__).resolve().parent

STAGE_NAMES = {
    "facts": "Product analysis",
    "market": "Market research",
    "pricing": "Pricing strategy",
    "copy": "Sales copy",
    "review": "Buyer perspective review",
}


@dataclass
class WorkflowState:
    """Store conversation inputs and results; the copy stage holds sales copy."""

    stage_names: ClassVar[dict[str, str]] = STAGE_NAMES

    request: str
    store: StoreConfig
    mode: Literal["preview", "draft", "publish"] = "preview"
    quantity: int = 1
    image_urls: list[str] = field(default_factory=list)
    image_ids: list[int] = field(default_factory=list)
    supplied_references: str = ""
    results: dict[str, BaseModel] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    receipt: dict | None = None
    payload: dict | None = None
    web_lookups: list[dict] = field(default_factory=list)

    def required_questions(self, proposed: list[str]) -> list[str]:
        """Keep only questions that prevent the listing from proceeding."""
        if self.receipt:
            return []
        facts = self.results.get("facts")
        pricing = self.results.get("pricing")
        review = self.results.get("review")
        if facts and facts.missing_questions:
            return facts.missing_questions
        if pricing and not pricing.suggested_price:
            return [
                "Please provide an asking price, or comparable products and prices for pricing."
            ]
        if review:
            if review.approved and not review.issues:
                return []
            return proposed or review.issues or review.suggested_changes
        return proposed

    def public_context(self) -> str:
        """Provide current inputs and results without exposing store credentials."""
        return json.dumps(
            {
                "user_request": self.request,
                "currency": self.store.currency,
                "mode": self.mode,
                "quantity": self.quantity,
                "seller_image_urls": self.image_urls,
                "seller_image_ids": self.image_ids,
                "user_supplied_market_material": self.supplied_references,
                "current_results": {key: value.model_dump() for key, value in self.results.items()},
                "submission_receipt": self.receipt,
            },
            ensure_ascii=False,
        )

    def export(self, answer: ManagerAnswer | None = None) -> dict:
        """Export analysis, source records, and the actual submission status."""
        return {
            "request": self.request,
            "mode": self.mode,
            "currency": self.store.currency,
            "results": {key: value.model_dump() for key, value in self.results.items()},
            "answer": answer.model_dump() if answer else None,
            "woocommerce_payload": self.payload,
            "submission_receipt": self.receipt,
            "tool_events": self.events,
            "web_lookups": self.web_lookups,
        }


def parser() -> argparse.ArgumentParser:
    """Define command options, listing modes, and argument types."""
    argument_parser = argparse.ArgumentParser(
        description="WooCommerce multi-agent listing assistant"
    )
    commands = argument_parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Check agent and tool structure without external services")
    run_parser = commands.add_parser(
        "run", help="Run the manager; generate a local preview by default"
    )
    source = run_parser.add_mutually_exclusive_group()
    source.add_argument("--request", help="Seller request and confirmed product facts")
    source.add_argument("--request-file", type=Path, help="UTF-8 product description file")
    run_parser.add_argument(
        "--references", type=Path, help="Optional market reference file (JSON or text)"
    )
    run_parser.add_argument(
        "--no-web", action="store_true", help="Disable web search; use supplied materials only"
    )
    run_parser.add_argument("--mode", choices=["preview", "draft", "publish"], default="preview")
    run_parser.add_argument(
        "--image-url", action="append", default=[], help="Seller product image URL; repeatable"
    )
    run_parser.add_argument(
        "--image-id",
        action="append",
        type=int,
        default=[],
        help="Existing WordPress media ID; repeatable",
    )
    run_parser.add_argument("--quantity", type=int, default=1)
    run_parser.add_argument(
        "--interactive",
        action="store_true",
        help="Allow follow-up input when essential facts are missing",
    )
    run_parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "latest.json")
    return argument_parser


def store_config() -> StoreConfig:
    """Read store credentials and currency from the environment."""
    return StoreConfig(
        url=os.getenv("WOOCOMMERCE_URL", ""),
        consumer_key=os.getenv("WOOCOMMERCE_CONSUMER_KEY", ""),
        consumer_secret=os.getenv("WOOCOMMERCE_CONSUMER_SECRET", ""),
        currency=os.getenv("STORE_CURRENCY", "CNY").upper(),
    )


def save_output(path: Path, data: dict) -> None:
    """Save the current results as UTF-8 JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def run(args: argparse.Namespace) -> int:
    """Run the team and continue after required seller supplements."""
    config = store_config()
    model = create_model()
    request = read_request(args)
    references = args.references.read_text(encoding="utf-8-sig") if args.references else ""
    state = WorkflowState(
        request=request,
        store=config,
        mode=args.mode,
        quantity=args.quantity,
        image_urls=args.image_url,
        image_ids=args.image_id,
        supplied_references=references,
    )
    manager = build_team(state, model, enable_web=not args.no_web)
    print(f"Mode: {args.mode}; currency: {config.currency}; model: {model.model}")
    print(
        "Market sources: "
        + (
            "Seller-supplied materials."
            if args.no_web
            else "Web search and seller-supplied materials."
        )
    )
    answer = None
    history = request
    try:
        while True:
            result = await Runner.run(
                manager,
                history,
                max_turns=24,
                run_config=RunConfig(tracing_disabled=True),
            )
            answer = result.final_output
            answer.questions = state.required_questions(answer.questions)
            if answer.questions:
                answer.message = "The product has not been submitted. Please provide the required information below."
            print("\n" + answer.message)
            save_output(args.output, state.export(answer))
            if not answer.questions:
                break
            print("\nRequired information:")
            for question in answer.questions:
                print("- " + question)
            if not args.interactive:
                break
            supplement = input("\nAdditional information (press Enter to finish):\n> ").strip()
            if not supplement:
                break
            state.request += "\nSeller supplement:" + supplement
            history = result.to_input_list() + [{"role": "user", "content": supplement}]
    finally:
        # Preserve completed analysis and submission receipts after interruption.
        save_output(args.output, state.export(answer))
    print(f"\nResults saved: {args.output.resolve()}")
    if state.receipt:
        print("Product result: " + state.receipt["message"])
        if state.receipt.get("permalink"):
            print("Product link: " + state.receipt["permalink"])
    return 1 if state.receipt and state.receipt["status"] == "unknown" else 0


def read_request(args: argparse.Namespace) -> str:
    """Read product details from a file, command argument, or the terminal."""
    if args.request_file:
        request = args.request_file.read_text(encoding="utf-8-sig")
    elif args.request:
        request = args.request
    else:
        request = input("Describe the product you want to sell:\n> ")
        args.interactive = True
    if not request.strip():
        raise ValueError("The product description must not be empty.")
    return request


def check_team() -> int:
    """List the manager and tools without calling external services."""
    team = build_team(
        WorkflowState(request="Offline structure check", store=StoreConfig()),
        model="deepseek-flash",
    )
    print("Main agent: " + team.name)
    for tool, label in zip(team.tools[:-1], STAGE_NAMES.values(), strict=False):
        print(f"  Specialist tool: {tool.name} ({label})")
    print("  Function tool: submit_listing (submit product)")
    print("Structure check passed.")
    return 0


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    args = parser().parse_args()
    try:
        if args.command == "check":
            return check_team()
        return asyncio.run(run(args))
    except Exception as exc:
        print(f"Run failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
