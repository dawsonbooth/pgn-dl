"""chessreps sign-in: its Supabase project on the shared session.

The session, its cookie and its refresh are ``pgn_dl.supabase``'s. A run makes
one data request, so the session is refreshed, if stale, once per run.
"""

from __future__ import annotations

from ...supabase import SupabaseAuth

#: The one cookie chessreps's SvelteKit server reads the session from. The
#: project is served from chessreps's own domain, so the cookie is named after
#: that (``api``) rather than after a Supabase project reference.
COOKIE_NAME = "sb-api-auth-token"

# Supabase anon keys are public: they ship in every page of the site and
# identify the project rather than authorising anything on their own.
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImpreG1yeGVlZXFscGV0b3RkY2l3Iiwicm9sZSI6"
    "ImFub24iLCJpYXQiOjE3MzQ4MTY1NTYsImV4cCI6MjA1MDM5MjU1Nn0."
    "K1-eR7j9WhVOOEKhOo91LzyW2XMBF8XeVX90BT7zfHs"
)

LOGIN_URL = "https://www.chessreps.com/signin"

session = SupabaseAuth(
    "chessreps",
    auth_url="https://api.chessreps.com/auth/v1",
    anon_key=ANON_KEY,
    cookie_name=COOKIE_NAME,
    site="https://www.chessreps.com",
)
session_cookie = session.cookie


async def login() -> None:
    """Open a browser so the user can sign in, then store the session."""
    await session.sign_in(login_url=LOGIN_URL)
