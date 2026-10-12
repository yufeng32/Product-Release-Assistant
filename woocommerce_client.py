"""Build WooCommerce product data and return creation receipts."""

import html
import ssl
from dataclasses import dataclass

import httpx

from models import ListingCopy, PricingAdvice


@dataclass
class StoreConfig:
    url: str = ""
    consumer_key: str = ""
    consumer_secret: str = ""
    currency: str = "CNY"


def build_payload(
    listing: ListingCopy,
    pricing: PricingAdvice,
    *,
    quantity: int,
    image_urls: list[str],
    status: str,
    image_ids: list[int] | None = None,
) -> dict:
    """Build product data from copy, pricing, inventory, and seller images."""
    description = "<p>" + html.escape(listing.description).replace("\n", "<br>\n") + "</p>"
    return {
        "name": listing.title.strip(),
        "type": "simple",
        "status": status,
        "regular_price": pricing.suggested_price,
        "description": description,
        "short_description": html.escape(listing.short_description),
        "manage_stock": True,
        "stock_quantity": quantity,
        "images": (
            [{"id": image_id} for image_id in (image_ids or [])]
            + [{"src": url} for url in image_urls]
        ),
    }


async def create_product(
    config: StoreConfig,
    payload: dict,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Send one creation request and propagate any HTTP client errors."""
    verify = ssl.create_default_context()
    base_url = config.url.rstrip("/") + "/wp-json/wc/v3/"
    async with httpx.AsyncClient(
        base_url=base_url,
        auth=(config.consumer_key, config.consumer_secret),
        timeout=30,
        follow_redirects=False,
        verify=verify,
        transport=transport,
    ) as client:
        response = await client.post("products", json=payload)
        response.raise_for_status()
        data = response.json()
        return {
            "status": data["status"],
            "product_id": data["id"],
            "permalink": data.get("permalink", ""),
            "message": "Product created. Draft links may require a store admin login to view.",
        }
