"""
eBay Pricing System - Pricing Analysis
"""
from __future__ import annotations

from typing import List, Optional

from .models import (
    ListingItem, SoldItem, ListingSummary, SoldSummary,
    DetailedSoldSummary, ConditionType
)


def analyze_listings(listings: List[ListingItem]) -> ListingSummary:
    new_totals: List[float] = []
    used_totals: List[float] = []

    for item in listings:
        total = item.total_price
        if item.condition == ConditionType.NEW:
            new_totals.append(total)
        else:
            used_totals.append(total)

    return ListingSummary(
        new_min_total=round(min(new_totals), 2) if new_totals else None,
        new_max_total=round(max(new_totals), 2) if new_totals else None,
        new_count=len(new_totals),
        used_min_total=round(min(used_totals), 2) if used_totals else None,
        used_max_total=round(max(used_totals), 2) if used_totals else None,
        used_count=len(used_totals),
    )


def analyze_sold_items(sold_items: List[SoldItem], detailed: bool = False) -> SoldSummary | DetailedSoldSummary:
    new_totals: List[float] = []
    used_totals: List[float] = []

    used_by_condition: dict[ConditionType, List[float]] = {
        ConditionType.USED_ACCEPTABLE: [],
        ConditionType.USED_GOOD: [],
        ConditionType.USED_VERY_GOOD: [],
        ConditionType.USED_LIKE_NEW: [],
    }

    for item in sold_items:
        total = item.sold_total
        if item.condition == ConditionType.NEW:
            new_totals.append(total)
        else:
            used_totals.append(total)
            if item.condition in used_by_condition:
                used_by_condition[item.condition].append(total)

    sold_new_avg = round(sum(new_totals) / len(new_totals), 2) if new_totals else None
    sold_used_avg = round(sum(used_totals) / len(used_totals), 2) if used_totals else None

    if not detailed:
        return SoldSummary(
            sold_new_avg_total=sold_new_avg,
            sold_new_count=len(new_totals),
            sold_used_avg_total=sold_used_avg,
            sold_used_count=len(used_totals),
        )

    def _avg(xs: List[float]) -> Optional[float]:
        return round(sum(xs) / len(xs), 2) if xs else None

    return DetailedSoldSummary(
        sold_new_avg_total=sold_new_avg,
        sold_new_count=len(new_totals),
        sold_used_avg_total=sold_used_avg,
        sold_used_count=len(used_totals),
        sold_used_acceptable_avg_total=_avg(used_by_condition[ConditionType.USED_ACCEPTABLE]),
        sold_used_acceptable_count=len(used_by_condition[ConditionType.USED_ACCEPTABLE]),
        sold_used_good_avg_total=_avg(used_by_condition[ConditionType.USED_GOOD]),
        sold_used_good_count=len(used_by_condition[ConditionType.USED_GOOD]),
        sold_used_very_good_avg_total=_avg(used_by_condition[ConditionType.USED_VERY_GOOD]),
        sold_used_very_good_count=len(used_by_condition[ConditionType.USED_VERY_GOOD]),
        sold_used_like_new_avg_total=_avg(used_by_condition[ConditionType.USED_LIKE_NEW]),
        sold_used_like_new_count=len(used_by_condition[ConditionType.USED_LIKE_NEW]),
    )


def get_condition_sold_average(sold_items: List[SoldItem], condition: ConditionType) -> Optional[float]:
    totals = [it.sold_total for it in sold_items if it.condition == condition]
    return round(sum(totals) / len(totals), 2) if totals else None
