"""Helper script to inspect the Gemini Enterprise / Discovery Engine A2A Gateway registration in Google Cloud."""

import argparse
import json
import os
from dotenv import load_dotenv
import google.auth
import google.auth.transport.requests
import httpx

load_dotenv()


def inspect_gcp_dcr_registration(project_id: str) -> dict:
    """Discovers the DCR Authorization & Gemini Enterprise A2A Agent registration in GCP."""
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
        auth_url = f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_id}/locations/global/authorizations"
        r_auth = client.get(auth_url, headers=headers)
        if r_auth.status_code == 200:
            info["authorizations"] = r_auth.json().get("authorizations", [])

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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect Gemini Enterprise / Discovery Engine A2A Gateway registration in Google Cloud"
    )
    parser.add_argument(
        "--project-id",
        default=os.getenv(
            "CHAINLINK_QUOTA_PROJECT",
            os.getenv("GOOGLE_CLOUD_PROJECT", "your-gcp-project-id"),
        ),
        help="GCP Project ID where the Chainlink Marketplace subscription is registered",
    )
    parser.add_argument(
        "--inspect-gcp",
        action="store_true",
        help="Inspect existing DCR Authorization & Gemini Enterprise A2A Gateway in GCP",
    )
    args = parser.parse_args()
    info = inspect_gcp_dcr_registration(args.project_id)
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
