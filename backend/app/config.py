import os

from dotenv import load_dotenv

load_dotenv()

EXTRACTION_MODEL = os.getenv("ATLAS_EXTRACTION_MODEL", "gpt-5-mini")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")

# Privacy guardrail, not a client-adjustable setting: how far back "recent"
# unread mail reaches. Deliberately not exposed as a /gmail/candidates query
# param -- a caller can ask for fewer results, never a wider sweep.
GMAIL_LOOKBACK_DAYS = int(os.getenv("ATLAS_GMAIL_LOOKBACK_DAYS", "30"))
