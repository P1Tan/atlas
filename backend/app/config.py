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
# "Daniel - Modern Assistant": swapped from the original "Henri - Express
# Host" (7.3) after live real-device testing -- Henri turned out to read as
# French-accented, not the "standard American accent" wanted. Chosen by
# querying Cartesia's real voice library again, this time checking the
# structured `accents` field directly (`{"accent": "general-american",
# "locale": "en-US", "is_native": true}`), not just the free-text
# description the original pick relied on -- confirms the accent for real
# rather than inferring it from wording. Description "Clear, crisp male
# voice for digital assistants and system interactions" also matches the
# persona framing well.
CARTESIA_VOICE_ID = os.getenv("ATLAS_CARTESIA_VOICE_ID", "47c38ca4-5f35-497b-b1a3-415245fb35e1")

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
# Found live (bug audit): /facts is authenticated (same as /chat and
# /voice/token) but had no rate limiting at all -- doesn't hit a paid
# LLM/embedding API the way those two do, but still allows unlimited-request
# hammering of the user_facts table by a single caller. Generous relative to
# /chat's limits (a cheap DB read/delete, not an LLM call), still bounded.
FACTS_RATE_LIMIT_PER_MINUTE = int(os.getenv("ATLAS_FACTS_RATE_LIMIT_PER_MINUTE", "60"))
FACTS_DAILY_USAGE_CAP = int(os.getenv("ATLAS_FACTS_DAILY_USAGE_CAP", "2000"))
# /gmail/candidates is the most expensive endpoint per call in the app: one
# LLM extraction per unread message, up to max_results (hard-capped at 20,
# the iOS client asks for 10), and a real check has been measured at ~60s
# for 9 messages. So the useful per-minute number is small -- a user simply
# cannot consume inbox checks faster than this by hand, and anything above
# it is a retry loop. Daily cap bounds the worst case at ~100 checks * 20
# messages = 2000 extractions, far beyond any plausible real day's use.
GMAIL_CANDIDATES_RATE_LIMIT_PER_MINUTE = int(
    os.getenv("ATLAS_GMAIL_CANDIDATES_RATE_LIMIT_PER_MINUTE", "5")
)
GMAIL_CANDIDATES_DAILY_USAGE_CAP = int(os.getenv("ATLAS_GMAIL_CANDIDATES_DAILY_USAGE_CAP", "100"))
# /extract is one LLM extraction per call, on text the user pasted or shared
# in -- interactive, so a handful a minute is already generous (the iOS app
# fires one per paste/share, never in a loop). Smaller per-minute number than
# /chat because there's no conversational back-and-forth driving repeat calls,
# larger than /gmail/candidates because a single call here is one extraction,
# not up to 20.
EXTRACT_RATE_LIMIT_PER_MINUTE = int(os.getenv("ATLAS_EXTRACT_RATE_LIMIT_PER_MINUTE", "10"))
EXTRACT_DAILY_USAGE_CAP = int(os.getenv("ATLAS_EXTRACT_DAILY_USAGE_CAP", "200"))
