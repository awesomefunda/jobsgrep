#!/bin/sh
set -e

echo ""
echo "  ⚡ JobsGrep"
echo "  ─────────────────────────────────"

# ─── Validate required env vars ──────────────────────────────────────────────
MISSING=""
if [ -z "$GROQ_API_KEY" ] && [ -z "$GEMINI_API_KEY" ]; then
  MISSING="$MISSING\n  • GROQ_API_KEY or GEMINI_API_KEY (needed for query parsing and scoring)"
fi
if [ -n "$MISSING" ]; then
  echo ""
  echo "  WARNING: The following recommended variables are not set:"
  printf "$MISSING\n"
  echo ""
  echo "  The app will start but searches will fail without an LLM key."
  echo "  Set them in your .env file or as environment variables."
  echo ""
fi

# ─── Auto-generate access token in PRIVATE mode ──────────────────────────────
MODE="${JOBSGREP_MODE:-LOCAL}"
if [ "$MODE" = "PRIVATE" ] && [ -z "$JOBSGREP_ACCESS_TOKEN" ]; then
  TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
  export JOBSGREP_ACCESS_TOKEN="$TOKEN"
  echo "  PRIVATE mode: generated access token (save this!):"
  echo "  JOBSGREP_ACCESS_TOKEN=$TOKEN"
  echo ""
fi

# ─── Ensure data directory exists ────────────────────────────────────────────
mkdir -p /root/.jobsgrep/reports

# ─── Print startup summary ───────────────────────────────────────────────────
echo "  Mode:    $MODE"
echo "  Port:    ${PORT:-8080}"
if [ -n "$GROQ_API_KEY" ];   then echo "  LLM:     Groq ✓"; fi
if [ -n "$GEMINI_API_KEY" ]; then echo "  LLM:     Gemini ✓"; fi
if [ "$MODE" = "PRIVATE" ];  then echo "  Auth:    Bearer token ✓"; fi
echo "  ─────────────────────────────────"
echo ""

exec python -m uvicorn jobsgrep.main:app \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-8080}" \
  --log-level info
