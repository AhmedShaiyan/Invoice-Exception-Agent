"""Shared password gate and spend guard for the Streamlit app (section 7).

One shared password, no per-user accounts -- appropriate for a portfolio
demo with a single owner, not a multi-tenant product. Streamlit re-runs
the whole script top-to-bottom on every interaction, so `require_auth()`
leans on `st.session_state` (survives reruns within one browser session)
rather than anything that would re-prompt on every click.
"""
import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def require_auth() -> None:
    """Call at the top of every page. Renders a password prompt and halts
    the rest of the page (st.stop()) until the correct password has been
    entered once this session."""
    if st.session_state.get("authenticated"):
        return
    st.title("Invoice Exception Agent")
    st.caption("Password-protected demo. See the README for access, or run it yourself locally.")
    _dbg = os.environ.get("APP_PASSWORD")
    if _dbg:
        import hashlib
        _h = hashlib.sha256(_dbg.encode()).hexdigest()[:16]
        _ascii = all(32 <= ord(c) < 127 for c in _dbg)
        st.caption(f"[TEMP DEBUG] len={len(_dbg)} ascii={_ascii} sha256_16={_h}")
    else:
        st.caption("[TEMP DEBUG] APP_PASSWORD not set")
    password = st.text_input("Password", type="password")
    if st.button("Enter"):
        expected = os.environ.get("APP_PASSWORD")
        if expected and password == expected:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()


def daily_limit() -> int:
    return int(os.environ.get("DAILY_API_LIMIT", "20"))
