"""Verify that only required information prompts the seller before resuming the workflow."""

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_workflow import FIXTURES

import main
from models import ManagerAnswer, PricingAdvice, ProductFacts, ReviewReport

SCHEMAS = {"facts": ProductFacts, "pricing": PricingAdvice, "review": ReviewReport}
READY = {stage: dict(FIXTURES[schema.__name__]) for stage, schema in SCHEMAS.items()}
RECEIPT = {"status": "draft", "product_id": 42, "message": "The draft has been created."}


class InteractionTests(unittest.IsolatedAsyncioTestCase):
    async def execute(
        self, turns, *, supplements=(), interactive=True, request="A used plastic cup, 10 CNY"
    ):
        histories = []

        async def respond(state, history, **kwargs):
            turn = turns[len(histories)]
            histories.append(history)
            state.results = {
                stage: SCHEMAS[stage].model_validate(data)
                for stage, data in turn["results"].items()
            }
            state.receipt = turn.get("receipt")
            return SimpleNamespace(
                final_output=ManagerAnswer(message=turn["message"], questions=turn["questions"]),
                to_input_list=lambda: [{"role": "user", "content": state.request}],
            )

        output = io.StringIO()
        with TemporaryDirectory() as folder:
            path = Path(folder) / "result.json"
            argv = ["run", "--request", request, "--mode", "draft", "--output", str(path)]
            if interactive:
                argv.append("--interactive")
            args = main.parser().parse_args(argv)
            with (
                patch("main.create_model", return_value=SimpleNamespace(model="offline")),
                patch("main.build_team", side_effect=lambda state, model, **kwargs: state),
                patch("main.Runner.run", new_callable=AsyncMock, side_effect=respond),
                patch("builtins.input", side_effect=supplements) as user_input,
                redirect_stdout(output),
            ):
                status = await main.run(args)
            saved = json.loads(path.read_text(encoding="utf-8"))
        return status, output.getvalue(), saved, histories, user_input

    async def test_created_draft_does_not_prompt_for_optional_details(self):
        turns = [
            {
                "results": READY,
                "receipt": RECEIPT,
                "message": "The draft has been created. Consider adding photos.",
                "questions": [
                    "Would you like to add photos?",
                    "Would you like to switch to publish?",
                ],
            }
        ]
        status, output, saved, histories, user_input = await self.execute(turns)
        self.assertEqual(status, 0)
        self.assertNotIn("Required information:", output)
        self.assertEqual(saved["answer"]["questions"], [])
        self.assertEqual(len(histories), 1)
        user_input.assert_not_called()

    async def test_approved_review_optional_suggestions_do_not_prompt(self):
        results = dict(
            READY, review=dict(READY["review"], suggested_changes=["Consider adding photos"])
        )
        turns = [
            {
                "results": results,
                "message": "The review passed. Consider adding photos.",
                "questions": ["Would you like to add photos?"],
            }
        ]
        _, output, saved, _, user_input = await self.execute(turns)
        self.assertNotIn("Required information:", output)
        self.assertEqual(saved["answer"]["questions"], [])
        user_input.assert_not_called()

    async def check_blocker(self, results, expected_question, supplement):
        turns = [
            {
                "results": results,
                "message": "The review passed, with no blocking issues.",
                "questions": [],
            },
            {
                "results": READY,
                "receipt": RECEIPT,
                "message": "The draft has been created after receiving the supplement.",
                "questions": [],
            },
        ]
        status, output, saved, histories, user_input = await self.execute(
            turns, supplements=[supplement]
        )
        self.assertEqual(status, 0)
        self.assertIn(
            "The product has not been submitted. Please provide the required information below.",
            output,
        )
        self.assertIn("Required information:", output)
        self.assertIn(expected_question, output)
        self.assertNotIn("no blocking issues", output)
        self.assertEqual(len(histories), 2)
        self.assertEqual(histories[1][-1], {"role": "user", "content": supplement})
        self.assertIn(supplement, saved["request"])
        self.assertIn("\nSeller supplement:", saved["request"])
        self.assertEqual(saved["submission_receipt"]["status"], "draft")
        self.assertEqual(saved["answer"]["questions"], [])
        user_input.assert_called_once()

    async def test_missing_facts_prompt_and_continue_after_supplement(self):
        results = dict(READY, facts=dict(READY["facts"], missing_questions=["Is the cup damaged?"]))
        await self.check_blocker(
            results, "Is the cup damaged?", "The cup is undamaged and works normally."
        )

    async def test_missing_price_prompts_even_if_review_says_approved(self):
        results = dict(READY, pricing=dict(READY["pricing"], suggested_price=None))
        await self.check_blocker(
            results,
            "Please provide an asking price, or comparable products and prices for pricing.",
            "My asking price is 10 CNY.",
        )

    async def test_blocking_review_prompts_even_if_manager_omits_questions(self):
        results = dict(
            READY,
            review={
                "approved": False,
                "issues": ["Please confirm whether the cup rim is cracked."],
                "suggested_changes": [],
            },
        )
        await self.check_blocker(
            results,
            "Please confirm whether the cup rim is cracked.",
            "The cup rim has a crack. Include it in the listing.",
        )

    async def test_noninteractive_run_lists_blocker_without_reading_input(self):
        results = {
            "facts": dict(
                READY["facts"], missing_questions=["Please describe the product's condition."]
            )
        }
        turns = [
            {
                "results": results,
                "message": "The product's condition needs confirmation.",
                "questions": [],
            }
        ]
        _, output, saved, histories, user_input = await self.execute(turns, interactive=False)
        self.assertIn("Required information:", output)
        self.assertEqual(saved["answer"]["questions"], ["Please describe the product's condition."])
        self.assertEqual(len(histories), 1)
        user_input.assert_not_called()

    async def test_multilingual_seller_request_survives_cli_and_export(self):
        request = "\u4e00\u4e2a\u7528\u8fc7\u7684\u676f\u5b50\uff0c10\u5143"
        turns = [
            {
                "results": READY,
                "receipt": RECEIPT,
                "message": "The draft has been created.",
                "questions": [],
            }
        ]
        status, _, saved, histories, user_input = await self.execute(turns, request=request)
        self.assertEqual(status, 0)
        self.assertEqual(saved["request"], request)
        self.assertEqual(histories[0], request)
        user_input.assert_not_called()
