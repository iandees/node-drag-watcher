"""One-time helper to obtain an OSM OAuth2 access token.

1. Register an app at https://www.openstreetmap.org/oauth2/applications/new
   - Redirect URI: urn:ietf:wg:oauth:2.0:oob
   - Scopes: write_api
2. Run this script with your client ID and secret.
3. Approve in the browser, paste the authorization code.
4. Set the printed token as OSM_ACCESS_TOKEN.
"""

import argparse
import urllib.parse
import webbrowser

import requests

OSM_BASE = "https://www.openstreetmap.org"
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"


def main():
    parser = argparse.ArgumentParser(description="Get an OSM OAuth2 access token")
    parser.add_argument("--client-id", required=True, help="OAuth2 client ID")
    parser.add_argument("--client-secret", required=True, help="OAuth2 client secret")
    args = parser.parse_args()

    authorize_url = (
        f"{OSM_BASE}/oauth2/authorize?"
        + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": args.client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": "write_api",
        })
    )

    print(f"Opening browser to authorize...\n{authorize_url}\n")
    webbrowser.open(authorize_url)

    code = input("Paste the authorization code here: ").strip()

    resp = requests.post(
        f"{OSM_BASE}/oauth2/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]

    print(f"\nAccess token:\n{token}\n")
    print("Set it as an environment variable:")
    print(f"  export OSM_ACCESS_TOKEN={token}")


if __name__ == "__main__":
    main()
