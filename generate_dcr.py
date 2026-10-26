"""Helper script for Chainlink Data Streams A2A Agent DCR & OAuth Token Generation.

Supports two workflows:
1. OAuth 2.0 Client Credentials Exchange:
   Given `CHAINLINK_CLIENT_ID` and `CHAINLINK_CLIENT_SECRET` (obtained via DCR),
   exchanges them at `POST {CHAINLINK_A2A_URL}/oauth/token` for a Bearer JWT
   (`CHAINLINK_A2A_TOKEN`).

2. RFC 7591 Dynamic Client Registration (`POST {CHAINLINK_A2A_URL}/dcr`):
   Signs a `software_statement` JWT using Google Cloud IAM Credentials API
   (`signJwt`) with your Marketplace `order_id` and `procurement_account_id`,
   calls `POST /dcr` to obtain `client_id` and `client_secret`, and exchanges
   them for `CHAINLINK_A2A_TOKEN`.
"""

import argparse
import json
import os
import time
from dotenv import load_dotenv
import httpx

load_dotenv()

DEFAULT_A2A_URL = os.getenv(
    "CHAINLINK_A2A_URL",
    "https://<YOUR_CHAINLINK_A2A_AGENT_URL>",
)


def exchange_client_credentials(
    a2a_url: str, client_id: str, client_secret: str
) -> dict:
    """Exchanges DCR client_id and client_secret for an OAuth Bearer JWT."""
    token_url = f"{a2a_url.rstrip('/')}/oauth/token"
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        resp.raise_for_status()
        return resp.json()


def sign_software_statement_with_iam(
    service_account_email: str,
    procurement_account_id: str,
    order_id: str,
    aud: str = "https://chain.link",
) -> str:
    """Signs a software_statement JWT via Google Cloud IAM Credentials API."""
    from google.cloud import iam_credentials_v1

    now = int(time.time())
    payload = {
        "iss": (
            "https://www.googleapis.com/service_accounts/v1/metadata/x509/"
            f"{service_account_email}"
        ),
        "iat": now,
        "exp": now + 3600,
        "aud": aud,
        "auth_app_redirect_uris": [
            "https://vertexaisearch.cloud.google.com/oauth-redirect"
        ],
        "sub": procurement_account_id,
        "google": {
            "order": order_id,
        },
    }

    iam_client = iam_credentials_v1.IAMCredentialsClient()
    name = f"projects/-/serviceAccounts/{service_account_email}"
    response = iam_client.sign_jwt(name=name, payload=json.dumps(payload))
    return response.signed_jwt


def call_dcr_endpoint(a2a_url: str, software_statement: str) -> dict:
    """Calls POST {a2a_url}/dcr with a signed software_statement JWT."""
    dcr_url = f"{a2a_url.rstrip('/')}/dcr"
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(
            dcr_url,
            json={"software_statement": software_statement},
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"DCR failed with HTTP {resp.status_code}: {resp.text}"
            )
        return resp.json()


def inspect_gcp_dcr_registration(project_id: str) -> dict:
    """Discovers the DCR Authorization & Gemini Enterprise A2A Agent registration in GCP."""
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "X-Goog-User-Project": project_id,
    }

    info = {"project_id": project_id, "authorizations": [], "registered_a2a_agents": []}
    with httpx.Client(timeout=20.0) as client:
        # 1. List Discovery Engine DCR Authorizations
        auth_url = f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_id}/locations/global/authorizations"
        r_auth = client.get(auth_url, headers=headers)
        if r_auth.status_code == 200:
            info["authorizations"] = r_auth.json().get("authorizations", [])

        # 2. List Gemini Enterprise Engines & Registered Agents
        eng_url = f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_id}/locations/global/collections/default_collection/engines"
        r_eng = client.get(eng_url, headers=headers)
        if r_eng.status_code == 200:
            for eng in r_eng.json().get("engines", []):
                eng_name = eng["name"]
                ag_url = f"https://discoveryengine.googleapis.com/v1alpha/{eng_name}/assistants/default_assistant/agents"
                r_ag = client.get(ag_url, headers=headers)
                if r_ag.status_code == 200:
                    for ag in r_ag.json().get("agents", []):
                        ag_name = ag["name"]
                        info["registered_a2a_agents"].append(
                            {
                                "displayName": ag.get("displayName"),
                                "name": ag_name,
                                "state": ag.get("state"),
                                "a2a_stream_endpoint": f"https://discoveryengine.googleapis.com/v1/{ag_name}/a2a/v1/message:stream",
                                "authorizations": ag.get("authorizationConfig", {}).get(
                                    "toolAuthorizations", []
                                ),
                            }
                        )
    return info


def main():
    parser = argparse.ArgumentParser(
        description="Generate, exchange, or inspect DCR credentials for Chainlink A2A Agent"
    )
    parser.add_argument(
        "--a2a-url",
        default=DEFAULT_A2A_URL,
        help="Chainlink A2A Agent base URL",
    )
    parser.add_argument(
        "--project-id",
        default=os.getenv(
            "CHAINLINK_QUOTA_PROJECT",
            os.getenv("GOOGLE_CLOUD_PROJECT", "your-gcp-project-id"),
        ),
        help="GCP Project ID where Chainlink Marketplace subscription is registered",
    )
    parser.add_argument(
        "--inspect-gcp",
        action="store_true",
        help="Inspect existing DCR Authorization & Gemini Enterprise A2A Gateway in GCP",
    )
    parser.add_argument(
        "--client-id",
        default=os.getenv("CHAINLINK_CLIENT_ID"),
        help="Existing DCR client_id to exchange for a Bearer token",
    )
    parser.add_argument(
        "--client-secret",
        default=os.getenv("CHAINLINK_CLIENT_SECRET"),
        help="Existing DCR client_secret to exchange for a Bearer token",
    )
    parser.add_argument(
        "--software-statement",
        help="Pre-signed software_statement JWT to send to POST /dcr",
    )
    parser.add_argument(
        "--order-id",
        help="GCP Marketplace Order ID (google.order claim) for IAM signJwt",
    )
    parser.add_argument(
        "--procurement-account-id",
        help="GCP Marketplace Procurement Account ID (sub claim) for IAM signJwt",
    )
    parser.add_argument(
        "--signer-sa",
        help="Service Account email used to sign software_statement JWT via IAM",
    )
    args = parser.parse_args()

    if args.inspect_gcp:
        print(f"[*] Inspecting DCR registration in GCP project '{args.project_id}'...")
        info = inspect_gcp_dcr_registration(args.project_id)
        print(json.dumps(info, indent=2))
        return

    client_id = args.client_id
    client_secret = args.client_secret

    # Step 1: Perform DCR if software_statement or signer-sa is provided
    if args.software_statement or (
        args.signer_sa and args.order_id and args.procurement_account_id
    ):
        stmt = args.software_statement
        if not stmt:
            print(f"[*] Signing software_statement via IAM SA: {args.signer_sa}")
            stmt = sign_software_statement_with_iam(
                service_account_email=args.signer_sa,
                procurement_account_id=args.procurement_account_id,
                order_id=args.order_id,
            )
        print(f"[*] Calling POST {args.a2a_url.rstrip('/')}/dcr ...")
        dcr_resp = call_dcr_endpoint(args.a2a_url, stmt)
        print(f"[+] DCR Success:\n{json.dumps(dcr_resp, indent=2)}")
        client_id = dcr_resp.get("client_id")
        client_secret = dcr_resp.get("client_secret")

    # Step 2: Exchange client_id & client_secret for Bearer access_token
    if client_id and client_secret:
        print(
            f"[*] Exchanging client_id & client_secret at "
            f"POST {args.a2a_url.rstrip('/')}/oauth/token ..."
        )
        token_resp = exchange_client_credentials(
            args.a2a_url, client_id, client_secret
        )
        print(f"[+] OAuth Token Response:\n{json.dumps(token_resp, indent=2)}")
        access_token = token_resp.get("access_token")
        if access_token:
            print(f"\nAdd this to your .env:\nCHAINLINK_A2A_TOKEN={access_token}")
    else:
        print(
            f"[*] No local client_secret provided. Inspecting GCP DCR registration in '{args.project_id}'..."
        )
        info = inspect_gcp_dcr_registration(args.project_id)
        print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
