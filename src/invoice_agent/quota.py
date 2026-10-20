"""Shared daily spend guard for pages that make real Claude API calls
(Upload Invoice, Draft vendor query). Shared across every visitor on
purpose: a public deployment with no per-user accounts needs one cap
everyone draws from, not a per-session counter a visitor could reset by
reloading. See invoice_agent.db.try_consume_daily_quota for the atomic
increment this guards."""
import os

from dotenv import load_dotenv

load_dotenv()


def daily_limit() -> int:
    return int(os.environ.get("DAILY_API_LIMIT", "20"))
