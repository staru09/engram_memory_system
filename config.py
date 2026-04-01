import os
from dotenv import load_dotenv

load_dotenv()

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

# PostgreSQL
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", "54345"))
PG_USER = os.getenv("PG_USER", "evermemos")
PG_PASSWORD = os.getenv("PG_PASSWORD", "evermemos")
PG_DB = os.getenv("PG_DB", "evermemos")

# Memory budgets (tokens)
PROFILE_TOKEN_BUDGET = int(os.getenv("PROFILE_TOKEN_BUDGET", "3000"))
SUMMARY_TOKEN_BUDGET = int(os.getenv("SUMMARY_TOKEN_BUDGET", "12000"))
COMPRESSION_THRESHOLD = float(os.getenv("COMPRESSION_THRESHOLD", "0.8"))  # 80%
