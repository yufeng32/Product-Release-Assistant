"""Typed results shared by the manager, specialists, and WooCommerce tool."""

from typing import Literal

from pydantic import BaseModel, Field


class ProductFacts(BaseModel):
    product_name: str
    confirmed_details: list[str]
    defects: list[str]
    selling_goal: str
    missing_questions: list[str] = Field(
        description=(
            "Essential questions for missing product facts. Leave an unspecified price to market "
            "research and pricing; photos and already confirmed facts do not require questions."
        )
    )


class PriceReference(BaseModel):
    title: str
    url: str
    price: str = Field(description="Price as a decimal string, or 'unknown'.")
    currency: str
    price_kind: Literal["asking", "sold", "unknown"]
    condition: str
    observed_at: str
    comparability: str


class MarketReport(BaseModel):
    references: list[PriceReference]
    summary: str
    limitations: list[str]


class PricingAdvice(BaseModel):
    suggested_price: str | None = Field(description="Decimal string; null if no defensible price.")
    currency: str
    rationale: str
    negotiation_advice: str
    confidence: Literal["low", "medium", "high"]


class ListingCopy(BaseModel):
    title: str
    short_description: str
    description: str = Field(description="Plain text, with honest condition and known defects.")
    photo_suggestions: list[str]


class ReviewReport(BaseModel):
    approved: bool
    issues: list[str]
    suggested_changes: list[str]


class ManagerAnswer(BaseModel):
    message: str
    questions: list[str] = Field(
        description=(
            "Essential questions the seller must answer before listing can continue. Exclude "
            "optional suggestions; return an empty list after completion or submission."
        )
    )
