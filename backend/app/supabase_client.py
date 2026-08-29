import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException
from supabase import Client, create_client

from app.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

logger = logging.getLogger("atlas.auth")


def get_supabase_client() -> Client:
    """The one Supabase client the backend uses -- always the service_role
    key (bypasses Row Level Security), since this is backend-only, trusted
    code. Never send this key to the iOS client. Constructed fresh per call,
    matching get_default_extractor()/get_default_weather_client()/
    get_default_web_search_client() -- no caching, for the same reason those
    don't cache their clients either."""
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


@dataclass
class AuthenticatedUser:
    id: str
    email: Optional[str]


def get_current_user(authorization: str = Header(default="")) -> AuthenticatedUser:
    """FastAPI dependency: validates the request's bearer token against
    Supabase Auth and returns the authenticated user, or raises 401.

    Validation is a real call to Supabase's auth server (not local JWT
    verification) -- avoids the backend needing to manage a separate signing
    secret, consistent with how every other external integration in this
    app (OpenAI, Google, Tavily, Open-Meteo) defers to the provider rather
    than reimplementing its logic locally.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    token = authorization[len("Bearer ") :]
    try:
        response = get_supabase_client().auth.get_user(token)
        if response is None or response.user is None:
            raise HTTPException(status_code=401, detail="invalid or expired token")
        return AuthenticatedUser(id=response.user.id, email=response.user.email)
    except HTTPException:
        raise
    except Exception:
        # Covers both a failed get_user() call and an unexpected response
        # shape (e.g. a user object missing an attribute) -- either way this
        # is an auth failure the client should see as 401, not a 500.
        logger.info("token validation failed")
        raise HTTPException(status_code=401, detail="invalid or expired token")
