import json
import logging

import redis.asyncio as redis
from openai import AsyncOpenAI
from redis.exceptions import RedisError

from app.config import Settings

logger = logging.getLogger(__name__)

# Matches the fixed taxonomy seeded by apps/core-api's Phase 3 migration
# exactly — a category name this returns that isn't in that table simply
# fails the get_category_id_by_name lookup and the transaction is left
# uncategorized, rather than silently miscategorized.
CATEGORY_TAXONOMY = [
    "Income",
    "Housing",
    "Transportation",
    "Food & Dining",
    "Shopping",
    "Entertainment",
    "Health",
    "Bills & Utilities",
    "Savings & Investments",
    "Transfer",
    "Other",
]

_RULES: dict[str, list[str]] = {
    "Food & Dining": [
        "restaurant", "cafe", "coffee", "starbucks", "diner", "bakery",
        "pizza", "grill", "bistro", "doordash", "ubereats", "grubhub",
        "mcdonald", "kfc",
    ],
    "Transportation": [
        "uber", "lyft", "gas station", "shell", "chevron", "exxon",
        "parking", "transit", "amtrak", "airlines", "airline",
    ],
    "Housing": ["rent", "mortgage", "hoa", "property management"],
    "Entertainment": [
        "netflix", "spotify", "hulu", "disney+", "movie", "cinema",
        "theater", "steam", "playstation", "xbox",
    ],
    "Bills & Utilities": [
        "electric", "water utility", "gas utility", "internet", "comcast",
        "verizon", "at&t", "t-mobile", "phone bill",
    ],
    "Health": ["pharmacy", "cvs", "walgreens", "clinic", "hospital", "dental", "doctor"],
    "Shopping": ["amazon", "walmart", "target", "costco", "best buy", "mall"],
    "Income": [
        "payroll", "direct deposit", "salary", "gusto", "ach electronic credit",
        "intrst pymnt", "interest payment",
    ],
    "Transfer": [
        "transfer", "venmo", "zelle", "paypal transfer", "automatic payment",
        "credit card", "card payment",
    ],
    "Savings & Investments": [
        "brokerage", "401k", "ira contribution", "investment transfer", "cd deposit",
    ],
}

_AI_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60


def categorize_by_rules(merchant_name: str) -> str | None:
    merchant_lower = merchant_name.lower()
    for category, keywords in _RULES.items():
        if any(keyword in merchant_lower for keyword in keywords):
            return category
    return None


async def categorize_with_ai_fallback(
    merchant_name: str, redis_client: redis.Redis, settings: Settings
) -> str | None:
    """Only called when the rules engine found no match. Returns None
    (leaves the transaction uncategorized) rather than guessing, if
    OpenAI isn't configured or the call fails for any reason — the same
    degrade-gracefully contract as every other optional integration."""
    if not settings.openai_api_key:
        return None

    cache_key = f"ai_category:{merchant_name.lower()}"
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except RedisError:
        logger.warning("AI category cache read failed for %s; calling OpenAI.", merchant_name)

    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=10,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify the merchant name into exactly one of these categories: "
                        f"{', '.join(CATEGORY_TAXONOMY)}. Respond with only the category name, "
                        "nothing else."
                    ),
                },
                {"role": "user", "content": merchant_name},
            ],
        )
        category = (response.choices[0].message.content or "").strip()
        if category not in CATEGORY_TAXONOMY:
            logger.warning("OpenAI returned an unrecognized category %r for %r", category, merchant_name)
            return None
    except Exception:
        logger.exception("OpenAI categorization call failed for %r", merchant_name)
        return None

    try:
        await redis_client.set(cache_key, json.dumps(category), ex=_AI_CACHE_TTL_SECONDS)
    except RedisError:
        logger.warning("AI category cache write failed for %s.", merchant_name)

    return category
