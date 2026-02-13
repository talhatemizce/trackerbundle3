"""
eBay Pricing System - Limit Calculations
"""
from __future__ import annotations

from .models import LimitConfig, AllLimits, ConditionType


def calculate_all_limits(config: LimitConfig) -> AllLimits:
    good = config.good_limit
    return AllLimits(
        new_limit=round(config.new_limit, 2),
        used_acceptable_limit=round(good * 0.80, 2),
        used_good_limit=round(good * 1.00, 2),
        used_very_good_limit=round(good * 1.10, 2),
        used_like_new_limit=round(good * 1.20, 2),
    )


def get_limit_for_condition(limits: AllLimits, condition: ConditionType) -> float:
    m = {
        ConditionType.NEW: limits.new_limit,
        ConditionType.USED_ACCEPTABLE: limits.used_acceptable_limit,
        ConditionType.USED_GOOD: limits.used_good_limit,
        ConditionType.USED_VERY_GOOD: limits.used_very_good_limit,
        ConditionType.USED_LIKE_NEW: limits.used_like_new_limit,
    }
    return m[condition]


def calculate_offer_ceiling(limit: float, multiplier: float = 1.30) -> float:
    return round(limit * multiplier, 2)
