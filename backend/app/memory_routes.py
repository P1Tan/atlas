import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.memory import MAX_FACT_LENGTH, MemoryStore, get_memory_store
from app.rate_limit import enforce_facts_rate_limit
from app.supabase_client import AuthenticatedUser, get_current_user

logger = logging.getLogger("atlas.memory")

router = APIRouter(prefix="/facts", tags=["memory"])


class Fact(BaseModel):
    id: str
    fact_text: str
    created_at: str


class FactUpdate(BaseModel):
    fact_text: str


@router.get("", response_model=List[Fact])
def list_facts(
    user: AuthenticatedUser = Depends(get_current_user),
    _rate_limit: None = Depends(enforce_facts_rate_limit),
    memory_store: MemoryStore = Depends(get_memory_store),
) -> List[Fact]:
    try:
        records = memory_store.list_fact_records(user.id)
    except Exception:
        logger.exception("failed to list facts")
        raise HTTPException(status_code=502, detail="failed to load remembered facts")
    return [Fact(id=r.id, fact_text=r.fact_text, created_at=r.created_at) for r in records]


@router.delete("/{fact_id}", status_code=204)
def delete_fact(
    fact_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    _rate_limit: None = Depends(enforce_facts_rate_limit),
    memory_store: MemoryStore = Depends(get_memory_store),
) -> None:
    try:
        uuid.UUID(fact_id)
    except ValueError:
        # A malformed id can never match a real row -- treat it the same as
        # "not found" rather than letting it reach the store as a raw
        # Postgres query error (a needless 502 + logged stack trace for what
        # is really just bad client input).
        raise HTTPException(status_code=404, detail="fact not found")

    try:
        deleted = memory_store.delete_fact(user.id, fact_id)
    except Exception:
        logger.exception("failed to delete fact")
        raise HTTPException(status_code=502, detail="failed to delete fact")
    if not deleted:
        raise HTTPException(status_code=404, detail="fact not found")


@router.patch("/{fact_id}", response_model=Fact)
def update_fact(
    fact_id: str,
    request: FactUpdate,
    user: AuthenticatedUser = Depends(get_current_user),
    _rate_limit: None = Depends(enforce_facts_rate_limit),
    memory_store: MemoryStore = Depends(get_memory_store),
) -> Fact:
    try:
        uuid.UUID(fact_id)
    except ValueError:
        # Same reasoning as delete_fact's uuid check above.
        raise HTTPException(status_code=404, detail="fact not found")

    # Trimmed before validating AND before storing, so trailing whitespace
    # from the editor can't sneak a fact past the empty check or over the
    # length cap -- the stored text is exactly what's validated here.
    fact_text = request.fact_text.strip()
    if not fact_text:
        raise HTTPException(status_code=422, detail="fact_text must not be empty")
    if len(fact_text) > MAX_FACT_LENGTH:
        raise HTTPException(
            status_code=422, detail=f"fact is too long (max {MAX_FACT_LENGTH} characters)"
        )

    try:
        record = memory_store.update_fact(user.id, fact_id, fact_text)
    except Exception:
        logger.exception("failed to update fact")
        raise HTTPException(status_code=502, detail="failed to update fact")
    if record is None:
        # No row matched BOTH the id and this user -- same deliberate
        # ambiguity as delete's 404, so the response never reveals that the
        # id exists under someone else's account.
        raise HTTPException(status_code=404, detail="fact not found")
    return Fact(id=record.id, fact_text=record.fact_text, created_at=record.created_at)
