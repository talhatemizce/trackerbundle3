"""
eBay Pricing System - Data Models (project-integrated)
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class ConditionType(str, Enum):
    NEW = "new"
    USED_ACCEPTABLE = "used_acceptable"
    USED_GOOD = "used_good"
    USED_VERY_GOOD = "used_very_good"
    USED_LIKE_NEW = "used_like_new"


class LimitConfig(BaseModel):
    new_limit: float = Field(..., description="Direct limit for Brand New items")
    good_limit: float = Field(..., description="Base limit for Used Good condition")


class AllLimits(BaseModel):
    new_limit: float
    used_acceptable_limit: float
    used_good_limit: float
    used_very_good_limit: float
    used_like_new_limit: float


class ListingItem(BaseModel):
    item_id: str
    condition: ConditionType
    item_price: float
    shipping_price: float = 0.0
    make_offer_enabled: bool = False

    @property
    def total_price(self) -> float:
        return round(self.item_price + self.shipping_price, 2)


class SoldItem(BaseModel):
    item_id: str
    condition: ConditionType
    sold_price: float
    sold_shipping: float = 0.0
    sold_date: Optional[str] = None

    @property
    def sold_total(self) -> float:
        return round(self.sold_price + self.sold_shipping, 2)


class ListingSummary(BaseModel):
    new_min_total: Optional[float] = None
    new_max_total: Optional[float] = None
    new_count: int = 0

    used_min_total: Optional[float] = None
    used_max_total: Optional[float] = None
    used_count: int = 0


class SoldSummary(BaseModel):
    sold_new_avg_total: Optional[float] = None
    sold_new_count: int = 0

    sold_used_avg_total: Optional[float] = None
    sold_used_count: int = 0


class DetailedSoldSummary(SoldSummary):
    sold_used_acceptable_avg_total: Optional[float] = None
    sold_used_acceptable_count: int = 0

    sold_used_good_avg_total: Optional[float] = None
    sold_used_good_count: int = 0

    sold_used_very_good_avg_total: Optional[float] = None
    sold_used_very_good_count: int = 0

    sold_used_like_new_avg_total: Optional[float] = None
    sold_used_like_new_count: int = 0


class EbaySummaryResponse(BaseModel):
    isbn: str
    limits: AllLimits
    active: ListingSummary
    sold: SoldSummary


class DecisionType(str, Enum):
    BUY = "BUY"
    OFFER = "OFFER"
    SKIP = "SKIP"


class ItemDecision(BaseModel):
    item_id: str
    condition: ConditionType
    total_price: float
    limit: float
    offer_ceiling: Optional[float] = None
    decision: DecisionType
    reason: str


class DecisionRequest(BaseModel):
    limits: LimitConfig
    listings: List[ListingItem]
    enable_offers: bool = True
    offer_multiplier: float = Field(default=1.30, description="Offer ceiling = limit * multiplier")
