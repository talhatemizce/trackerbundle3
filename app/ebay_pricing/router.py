"""
Project-integrated eBay Pricing API Router
Include into panel_api:app via include_router().
"""
from __future__ import annotations

import json
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .models import (
    LimitConfig, AllLimits, ListingItem, SoldItem,
    EbaySummaryResponse, ConditionType, ItemDecision
)
from .limits import calculate_all_limits
from .pricing import analyze_listings, analyze_sold_items, get_condition_sold_average
from .decision import evaluate_listings

router = APIRouter(prefix="/ebay", tags=["ebay"])


# -----------------------------
# Request Models (router-local)
# -----------------------------
class DecideRequest(BaseModel):
    isbn: Optional[str] = None
    new_limit: float = Field(..., gt=0)
    good_limit: float = Field(..., gt=0)

    listings: List[ListingItem] = Field(default_factory=list)

    enable_offers: bool = True
    offer_multiplier: float = Field(1.30, gt=1.0)


class DecideResponse(BaseModel):
    isbn: Optional[str] = None
    limits: AllLimits
    decisions: List[ItemDecision]


# -----------------------------
# Endpoints
# -----------------------------
@router.post("/limits", response_model=AllLimits)
def calculate_limits(config: LimitConfig):
    return calculate_all_limits(config)


@router.post("/decide", response_model=DecideResponse)
def decide(req: DecideRequest):
    """
    Decide BUY/OFFER/SKIP for each listing using derived limits.
    total_price = item_price + shipping_price (ListingItem.total_price)
    BUY: total <= limit
    OFFER: make_offer_enabled and total <= limit * offer_multiplier
    else SKIP
    """
    config = LimitConfig(new_limit=req.new_limit, good_limit=req.good_limit)
    limits = calculate_all_limits(config)

    decisions = evaluate_listings(
        listings=req.listings,
        limits=limits,
        enable_offers=req.enable_offers,
        offer_multiplier=req.offer_multiplier,
    )

    return DecideResponse(isbn=req.isbn, limits=limits, decisions=decisions)


@router.get("/sold_avg")
def sold_avg(
    condition: ConditionType = Query(..., description="Condition to average (e.g. NEW, USED_GOOD...)"),
    mock_sold: Optional[str] = Query(None, description="JSON list of sold items (for testing)")
):
    """
    Returns sold average total_price for a specific condition.
    If mock_sold provided, uses that data (testing mode).
    """
    sold_items: List[SoldItem] = []
    if mock_sold:
        try:
            sold_items = [SoldItem(**it) for it in json.loads(mock_sold)]
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid mock_sold: {e}")

    avg = get_condition_sold_average(sold_items, condition)
    return {"condition": condition, "sold_avg_total": avg}


@router.get("/summary", response_model=EbaySummaryResponse)
def summary(
    isbn: str,
    new_limit: float = Query(..., gt=0),
    good_limit: float = Query(..., gt=0),
    mock_listings: Optional[str] = Query(None, description="JSON list of listings (testing)"),
    mock_sold: Optional[str] = Query(None, description="JSON list of sold items (testing)"),
    detailed_sold: bool = Query(False, description="Include condition breakdown for used sold"),
):
    """
    Full summary: derived limits + active listings stats + sold stats (optional mocks)
    """
    config = LimitConfig(new_limit=new_limit, good_limit=good_limit)
    limits = calculate_all_limits(config)

    listings: List[ListingItem] = []
    if mock_listings:
        try:
            listings = [ListingItem(**it) for it in json.loads(mock_listings)]
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid mock_listings: {e}")

    sold_items: List[SoldItem] = []
    if mock_sold:
        try:
            sold_items = [SoldItem(**it) for it in json.loads(mock_sold)]
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid mock_sold: {e}")

    active_summary = analyze_listings(listings)
    sold_summary = analyze_sold_items(sold_items, detailed=detailed_sold)

    return EbaySummaryResponse(
        isbn=isbn,
        limits=limits,
        active=active_summary,
        sold=sold_summary,
    )
