import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.memory import MemoryStore, get_memory_store
from app.supabase_client import AuthenticatedUser, get_current_user

logger = logging.getLogger("atlas.memory")

router = APIRouter(prefix="/facts", tags=["memory"])


class Fact(BaseModel):
    id: str
    fact_text: str
    created_at: str


@router.get("", response_model=List[Fact])
def list_facts(
    user: AuthenticatedUser = Depends(get_current_user),
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
