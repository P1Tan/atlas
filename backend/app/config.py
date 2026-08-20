import os

from dotenv import load_dotenv

load_dotenv()

EXTRACTION_MODEL = os.getenv("ATLAS_EXTRACTION_MODEL", "gpt-5-mini")
