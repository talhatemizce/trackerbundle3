"""
eBay Pricing System - Decision Engine (BUY/OFFER/SKIP)
"""
from __future__ import annotations

from typing import List

from .models import ListingItem, AllLimits, ItemDecision, DecisionType
from .limits import get_limit_for_condition, calculate_offer_ceiling


def evaluate_listings(
    listings: List[ListingItem],
    limits: AllLimits,
    enable_offers: bool = True,
    offer_multiplier: float = 1.30,
) -> List[ItemDecision]:
    out: List[ItemDecision] = []

    for item in listings:
        total = item.total_price
        limit = get_limit_for_condition(limits, item.condition)

        if total <= limit:
            out.append(ItemDecision(
                item_id=item.item_id,
                condition=item.condition,
                total_price=total,
                limit=limit,
                offer_ceiling=None,
                decision=DecisionType.BUY,
                reason=f"Total {total} <= limit {limit}",
            ))
            continue

        if enable_offers and item.make_offer_enabled:
            ceiling = calculate_offer_ceiling(limit, offer_multiplier)
            if total <= ceiling:
                out.append(ItemDecision(
                    item_id=item.item_id,
                    condition=item.condition,
                    total_price=total,
                    limit=limit,
                    offer_ceiling=ceiling,
                    decision=DecisionType.OFFER,
                    reason=f"Total {total} <= offer_ceiling {ceiling}",
                ))
                continue

        out.append(ItemDecision(
            item_id=item.item_id,
            condition=item.condition,
            total_price=total,
            limit=limit,
            offer_ceiling=calculate_offer_ceiling(limit, offer_multiplier) if (enable_offers and item.make_offer_enabled) else None,
            decision=DecisionType.SKIP,
            reason=f"Total {total} exceeds limit{'' if not (enable_offers and item.make_offer_enabled) else ' and offer ceiling'}",
        ))

    return out
