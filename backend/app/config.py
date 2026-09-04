import os

from dotenv import load_dotenv

load_dotenv()

EXTRACTION_MODEL = os.getenv("ATLAS_EXTRACTION_MODEL", "gpt-5-mini")
CHAT_MODEL = os.getenv("ATLAS_CHAT_MODEL", "gpt-5-mini")
EMBEDDING_MODEL = os.getenv("ATLAS_EMBEDDING_MODEL", "text-embedding-3-small")

# The assistant's character (tone, address style) -- configuration, not
# hard-coded, per assistant-spec.md §10, so it can be tuned or swapped
# without touching code. Kept separate from operating instructions (tool
# usage, behavior rules) in app/chat.py, which aren't persona.
DEFAULT_PERSONA = (
    "You are Atlas, the user's personal assistant. Your tone is warm but "
    "efficient, with a touch of dry wit -- never saccharine, never verbose "
    "for its own sake. Address the user directly and plainly. You are "
    "capable and a little understated about it: you don't oversell what "
    "you're doing, you just do it well."
)
PERSONA = os.getenv("ATLAS_PERSONA", DEFAULT_PERSONA)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")

# Privacy guardrail, not a client-adjustable setting: how far back "recent"
# unread mail reaches. Deliberately not exposed as a /gmail/candidates query
# param -- a caller can ask for fewer results, never a wider sweep.
GMAIL_LOOKBACK_DAYS = int(os.getenv("ATLAS_GMAIL_LOOKBACK_DAYS", "30"))

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
# Bypasses Row Level Security -- backend-only, never sent to the iOS client.
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

# Milestone 7.1 scaffold only -- real per-session room/token issuance (via a
# /chat-style authenticated endpoint) is deferred until the iOS app actually
# initiates voice sessions (7.2+). For now this is a single fixed dev room
# you join manually via a browser test client to prove the pipeline works.
VOICE_DEV_ROOM_NAME = os.getenv("ATLAS_VOICE_DEV_ROOM_NAME", "atlas-dev")
VOICE_DEV_TIMEZONE = os.getenv("ATLAS_VOICE_DEV_TIMEZONE", "America/New_York")

CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")
# "Henri - Express Host": Cartesia's own description is "clear, warm, and
# efficient" -- chosen by querying Cartesia's real voice library (not
# guessed) for the closest match to PERSONA's "warm but efficient" framing
# in app/config.py.
CARTESIA_VOICE_ID = os.getenv("ATLAS_CARTESIA_VOICE_ID", "d9f4af15-c402-4f50-bbda-d8823d028d6a")

# Milestone 9.3 (cost/abuse guardrails, spec §18): per-user limits so a bug
# or abuse can't run up the bill, not precisely-tuned business numbers --
# generous enough for real interactive use, bounded enough to cap worst-case
# exposure. /chat is one call per user-sent message; /voice/token mints a
# fresh token per voice turn (VoiceSessionController fetches one at the start
# of every startVoiceTurn(), not once per app session), so its per-minute
# ceiling needs more headroom than /chat's despite gating a cheaper call.
CHAT_RATE_LIMIT_PER_MINUTE = int(os.getenv("ATLAS_CHAT_RATE_LIMIT_PER_MINUTE", "20"))
CHAT_DAILY_USAGE_CAP = int(os.getenv("ATLAS_CHAT_DAILY_USAGE_CAP", "300"))
VOICE_TOKEN_RATE_LIMIT_PER_MINUTE = int(os.getenv("ATLAS_VOICE_TOKEN_RATE_LIMIT_PER_MINUTE", "30"))
VOICE_TOKEN_DAILY_USAGE_CAP = int(os.getenv("ATLAS_VOICE_TOKEN_DAILY_USAGE_CAP", "500"))
