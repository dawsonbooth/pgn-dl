"""chessendings sign-in: its Supabase project on the shared session.

The session, its cookie and its refresh are ``pgn_dl.supabase``'s.
"""

from __future__ import annotations

from ...supabase import SupabaseAuth

PROJECT = "ndkofotnyjuqsfiicbnp"

#: The cookie the site's server reads the session from.
COOKIE_NAME = f"sb-{PROJECT}-auth-token"

# Public: it ships in every page of the site.
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5ka29mb3RueWp1cXNmaWljYm5wIiwicm9sZSI6"
    "ImFub24iLCJpYXQiOjE3ODMwMzg0NDcsImV4cCI6MjA5ODYxNDQ0N30."
    "7ZyanpVSW5D2CgjyABe6-FWgSP_dihy7yJ2_GI8RdbA"
)

LOGIN_URL = "https://chessendings.com/login"

session = SupabaseAuth(
    "chessendings",
    auth_url=f"https://{PROJECT}.supabase.co/auth/v1",
    anon_key=ANON_KEY,
    cookie_name=COOKIE_NAME,
    site="https://chessendings.com",
)
has_saved_session = session.has_saved_session
session_cookie = session.cookie


async def login() -> None:
    """Open a browser so the user can sign in, then store the session."""
    await session.sign_in(login_url=LOGIN_URL, display_name="ChessEndings")
