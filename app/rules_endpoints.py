from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict
from app import rules_store

router = APIRouter(prefix="/rules", tags=["Rules"])

class DefaultsUpdate(BaseModel):
    new_max: Optional[float] = Field(None, gt=0)
    used_all_max: Optional[float] = Field(None, gt=0)
    multipliers: Optional[Dict[str, float]] = None
    used: Optional[Dict[str, float]] = None

class ISBNUpdate(BaseModel):
    new_max: Optional[float] = Field(None, gt=0)
    used_all_max: Optional[float] = Field(None, gt=0)
    used: Optional[Dict[str, float]] = None  # {"good": 20, "very_good": 22} gibi

@router.get("")
def get_rules():
    return rules_store.load_rules()

@router.post("/defaults")
def update_defaults(payload: DefaultsUpdate):
    try:
        # rules_store.set_defaults signature:
        # (new_max=None, used_all_max=None, used_conditions=None)
        used_conditions = payload.used
        # multipliers şimdilik rules_store tarafında ayrıca set edilmiyor (mevcut fonksiyon imzası)
        # İstersen multipliers için ayrı fonksiyon ekleriz.
        return rules_store.set_defaults(
            new_max=payload.new_max,
            used_all_max=payload.used_all_max,
            used_conditions=used_conditions
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{isbn}")
def put_isbn(isbn: str, payload: ISBNUpdate):
    try:
        return rules_store.set_isbn_override(
            isbn=isbn,
            new_max=payload.new_max,
            used_all_max=payload.used_all_max,
            used_conditions=payload.used
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{isbn}")
def delete_isbn(isbn: str):
    return rules_store.delete_isbn_override(isbn)

@router.get("/effective")
def effective(isbn: Optional[str] = Query(None), condition: str = Query(...)):
    return rules_store.effective_limit(isbn, condition)
