"""
youtube_auth.py — One-time Google OAuth 2.0 setup for YouTube Data API v3.

Run this script once locally to obtain a refresh_token for YouTube uploads.
After running, copy the printed refresh_token into your GitHub Secrets as
YOUTUBE_REFRESH_TOKEN.

Usage:
    pip install google-auth-oauthlib python-dotenv
    python tools/youtube_auth.py
"""

import os
import sys

try:
    from dotenv import load_dotenv
except ImportError:
    print("ERROR: python-dotenv is not installed. Run: pip install python-dotenv")
    sys.exit(1)

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("ERROR: google-auth-oauthlib is not installed. Run: pip install google-auth-oauthlib")
    sys.exit(1)

# Load .env from project root (one level up from tools/)
dotenv_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(dotenv_path=dotenv_path)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main():
    client_id = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()

    if not client_id:
        print("ERROR: YOUTUBE_CLIENT_ID is not set. Add it to .env or set it as an env var.")
        sys.exit(1)

    if not client_secret:
        print("ERROR: YOUTUBE_CLIENT_SECRET is not set. Add it to .env or set it as an env var.")
        sys.exit(1)

    print("=== YouTube OAuth 2.0 Setup ===")
    print(f"  YOUTUBE_CLIENT_ID     : {client_id[:8]}...")
    print(f"  YOUTUBE_CLIENT_SECRET : {client_secret[:8]}...")
    print(f"  Scopes                : {SCOPES}")
    print()
    print("A browser window will open. Log in with the Google account that owns")
    print("the YouTube channel you want to upload to, then click Allow.")
    print()

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)

    # port=0 lets the OS pick a free port automatically
    credentials = flow.run_local_server(
        port=0,
        prompt="consent",
        access_type="offline",
        login_hint="prestopodcast@gmail.com",
    )

    refresh_token = credentials.refresh_token

    if not refresh_token:
        print()
        print("ERROR: No refresh_token was returned.")
        print("Make sure you authorized with a Google account that has a YouTube channel,")
        print("and that the OAuth consent screen is set to request offline access.")
        sys.exit(1)

    print()
    print("=" * 60)
    print("  OAuth SUCCESS")
    print("=" * 60)
    print()
    print("  Verification (first 8 chars only):")
    print(f"    YOUTUBE_CLIENT_ID     : {client_id[:8]}...")
    print(f"    YOUTUBE_CLIENT_SECRET : {client_secret[:8]}...")
    print()
    print("  Your refresh token:")
    print()
    print(f"    {refresh_token}")
    print()
    print("=" * 60)
    print()
    print("  NEXT STEPS:")
    print("  1. Go to your GitHub repo → Settings → Secrets and variables → Actions")
    print("  2. Create a secret named:  YOUTUBE_REFRESH_TOKEN")
    print("  3. Paste the refresh token above as the value")
    print("  4. Also ensure these secrets exist:")
    print("       YOUTUBE_CLIENT_ID")
    print("       YOUTUBE_CLIENT_SECRET")
    print()
    print("  The refresh token does not expire unless you revoke access at:")
    print("  https://myaccount.google.com/permissions")
    print()


if __name__ == "__main__":
    main()
