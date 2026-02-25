"""Experimental wallet/profile API for CardTrack.

This module is intentionally standalone so it won't conflict with
existing backend code. You can run it separately via:

    uvicorn app.cards_catalogue_api:app --reload

It implements basic CRUD for the user's wallet (credit cards) and
uses the same JSON storage format as data_service.py, while matching
API_CONTRACT.md's error response shape:

    {"error": {"code": "...", "message": "...", "details": {...}}}
"""

import csv
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, status, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

from app.services.user_profile import get_user_by_username, 

DEFAULT_USER_ID = "u_001"


def _load_cards_master_ids() -> List[str]:
    """Load card_id values from frontend/public/data/cards_master.csv.

    Falls back to an empty list if the CSV is missing.
    """
    try:
        backend_root = os.path.dirname(os.path.dirname(__file__))  # .../backend
        repo_root = os.path.dirname(backend_root)
        csv_path = os.path.join(
            repo_root,
            "frontend",
            "public",
            "data",
            "cards_master.csv",
        )
        if not os.path.exists(csv_path):
            return []

        ids: List[str] = []
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cid = (row.get("card_id") or "").strip()
                if cid:
                    ids.append(cid)
        return ids
    except Exception:
        # In a prototype setting, silently ignore and return empty list
        return []


CARDS_MASTER_IDS = set(_load_cards_master_ids())


class WalletCard(BaseModel):
    card_id: str
    refresh_day_of_month: int = Field(..., ge=1, le=31)
    annual_fee_billing_date: str  # YYYY-MM-DD
    cycle_spend_sgd: float = Field(0, ge=0)

    @validator("annual_fee_billing_date")
    def validate_annual_fee_billing_date(cls, v: str) -> str:
        """Ensure date is in ISO YYYY-MM-DD format, per API contract."""
        try:
            # fromisoformat will raise ValueError if format is invalid
            datetime.fromisoformat(v)
        except ValueError as exc:
            raise ValueError("annual_fee_billing_date must be YYYY-MM-DD") from exc
        return v


class WalletCardCreate(BaseModel):
    wallet_card: WalletCard


class WalletCardUpdate(BaseModel):
    refresh_day_of_month: Optional[int] = Field(None, ge=1, le=31)
    annual_fee_billing_date: Optional[str] = None
    cycle_spend_sgd: Optional[float] = Field(None, ge=0)


class WalletResponse(BaseModel):
    wallet: List[WalletCard]


class WalletCardResponse(BaseModel):
    wallet_card: WalletCard


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Dict[str, Any] = {}


class ErrorEnvelope(BaseModel):
    error: ErrorBody


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    body = ErrorEnvelope(
        error=ErrorBody(code=code, message=message, details=details or {}),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())

def _save_users(users: Dict[str, Any]) -> None:
    _save_json(USERS_FILE, users)


def _get_or_create_user(user_id: str) -> Dict[str, Any]:
    users = _get_users()
    user = users.get(user_id)
    if not user:
        user = {
            "user_id": user_id,
            "username": "demo",
            "preference": "miles",
            "wallet": [],
        }
        users[user_id] = user
        _save_users(users)
    return user


app = FastAPI(
    title="CardTrack Wallet API (Draft)",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request, exc: RequestValidationError):  # type: ignore[override]
    return _error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="VALIDATION_ERROR",
        message="Invalid request payload.",
        details={"errors": exc.errors()},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):  # type: ignore[override]
    """Normalize HTTPExceptions to the standard error envelope.

    If the detail already contains an `error` object, pass it through; otherwise
    wrap it to match API_CONTRACT.md.
    """
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    code = "INTERNAL_ERROR" if exc.status_code >= 500 else "VALIDATION_ERROR"
    message = str(exc.detail) if exc.detail else "Request failed."
    return _error_response(
        status_code=exc.status_code,
        code=code,
        message=message,
        details={},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):  # type: ignore[override]
    """Catch-all for unhandled errors, using contract-compliant shape."""
    return _error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        message="Internal server error.",
        details={},
    )


@app.get("/api/v1/wallet", response_model=WalletResponse)
def get_wallet() -> Dict[str, Any]:
    """Return the current user's wallet.

    Shape:
        {"wallet": [WalletCard, ...]}
    """
    users = _get_users()
    user = users.get(DEFAULT_USER_ID)
    if not user:
        return _error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="NOT_FOUND",
            message="Profile not found.",
            details={},
        )

    wallet_data = user.get("wallet", [])
    wallet_cards = [WalletCard(**c) for c in wallet_data]
    return {"wallet": [c.model_dump() for c in wallet_cards]}


@app.post("/api/v1/wallet", status_code=status.HTTP_201_CREATED, response_model=WalletCardResponse)
def add_wallet_card(payload: WalletCardCreate) -> Dict[str, Any]:
    """Add a new card to the user's wallet.

    Request body:
        {"wallet_card": { ...WalletCard fields... }}
    """
    card = payload.wallet_card

    # Business validation: card_id must exist in cards master
    if CARDS_MASTER_IDS and card.card_id not in CARDS_MASTER_IDS:
        return _error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="VALIDATION_ERROR",
            message=f"card_id '{card.card_id}' does not exist in cards master.",
            details={"field": "wallet_card.card_id"},
        )

    users = _get_users()
    user = users.get(DEFAULT_USER_ID) or _get_or_create_user(DEFAULT_USER_ID)
    wallet = user.get("wallet", [])

    # Prevent duplicates
    if any(wc.get("card_id") == card.card_id for wc in wallet):
        return _error_response(
            status_code=status.HTTP_409_CONFLICT,
            code="CONFLICT",
            message=f"card_id '{card.card_id}' already exists in wallet.",
            details={"field": "wallet_card.card_id"},
        )

    wallet.append(card.model_dump())
    user["wallet"] = wallet
    users[DEFAULT_USER_ID] = user
    _save_users(users)

    return {"wallet_card": card.model_dump()}


@app.patch("/api/v1/wallet/{card_id}", response_model=WalletCardResponse)
def update_wallet_card(card_id: str, payload: WalletCardUpdate) -> Dict[str, Any]:
    """Update fields of an existing wallet card.

    Only provided fields are updated.
    """
    users = _get_users()
    user = users.get(DEFAULT_USER_ID)
    if not user:
        return _error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="NOT_FOUND",
            message="Profile not found.",
            details={},
        )

    wallet = user.get("wallet", [])
    for wc in wallet:
        if wc.get("card_id") == card_id:
            if payload.refresh_day_of_month is not None:
                wc["refresh_day_of_month"] = payload.refresh_day_of_month
            if payload.annual_fee_billing_date is not None:
                wc["annual_fee_billing_date"] = payload.annual_fee_billing_date
            if payload.cycle_spend_sgd is not None:
                wc["cycle_spend_sgd"] = payload.cycle_spend_sgd

            user["wallet"] = wallet
            users[DEFAULT_USER_ID] = user
            _save_users(users)
            return {"wallet_card": WalletCard(**wc).model_dump()}

    return _error_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code="NOT_FOUND",
        message=f"card_id '{card_id}' not found in wallet.",
        details={},
    )


@app.delete("/api/v1/wallet/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_wallet_card(card_id: str) -> JSONResponse:
    """Remove a card from the user's wallet."""
    users = _get_users()
    user = users.get(DEFAULT_USER_ID)
    if not user:
        return _error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="NOT_FOUND",
            message="Profile not found.",
            details={},
        )

    wallet = user.get("wallet", [])
    new_wallet = [wc for wc in wallet if wc.get("card_id") != card_id]
    if len(new_wallet) == len(wallet):
        return _error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="NOT_FOUND",
            message=f"card_id '{card_id}' not found in wallet.",
            details={},
        )

    user["wallet"] = new_wallet
    users[DEFAULT_USER_ID] = user
    _save_users(users)

    # 204 with empty body
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)
