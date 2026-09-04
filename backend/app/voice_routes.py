"""Milestone 7.2b -- mints a LiveKit join token for a real human participant
(the iOS app) so it can join the same fixed dev room `app/voice_agent.py`
joins as the bot (see `VOICE_DEV_ROOM_NAME`, app/config.py).

Deliberately does NOT use `pipecat.runner.livekit.generate_token_with_agent`
(see app/voice_agent.py) -- that helper sets `agent=True` in the video grant,
a marker meant for the bot participant, not a human. This calls
`livekit.api.AccessToken` directly with a plain `room_join` grant instead,
mirroring that helper's own token-building shape (confirmed against its
installed source) minus the `agent=True` grant and `with_name`.
"""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from livekit import api
from pydantic import BaseModel

from app.config import LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_URL, VOICE_DEV_ROOM_NAME
from app.rate_limit import enforce_voice_token_rate_limit
from app.supabase_client import AuthenticatedUser, get_current_user

logger = logging.getLogger("atlas.voice")

router = APIRouter(prefix="/voice", tags=["voice"])


class VoiceTokenResponse(BaseModel):
    url: str
    room_name: str
    token: str


@router.post("/token", response_model=VoiceTokenResponse)
def create_voice_token(
    user: AuthenticatedUser = Depends(get_current_user),
    _rate_limit: None = Depends(enforce_voice_token_rate_limit),
) -> VoiceTokenResponse:
    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        raise HTTPException(status_code=503, detail="voice pipeline not configured")

    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(user.id)
        .with_grants(api.VideoGrants(room_join=True, room=VOICE_DEV_ROOM_NAME))
        .with_ttl(timedelta(hours=1))
        .to_jwt()
    )
    return VoiceTokenResponse(url=LIVEKIT_URL, room_name=VOICE_DEV_ROOM_NAME, token=token)
