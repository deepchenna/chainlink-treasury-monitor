import base64
import json
import os
import time
import uuid
import google.auth
import google.auth.transport.requests
import httpx

SUPPORTED_PAIRS = ["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD"]


def _extract_a2ui_feeds(payload: object) -> list[dict]:
    """Extracts Chainlink price feeds from a Discovery Engine A2UI payload."""
    if isinstance(payload, list):
        for item in payload:
            if feeds := _extract_a2ui_feeds(item):
                return feeds
    elif isinstance(payload, dict):
        inline = payload.get("inlineData", {})
        if inline.get("mimeType") == "application/json+a2ui" and inline.get("data"):
            decoded = json.loads(base64.b64decode(inline["data"]))
            kv = {
                item["key"]: item.get("valueString", "")
                for item in decoded.get("dataModelUpdate", {}).get("contents", [])
                if "key" in item
            }
            now = int(time.time())
            return [
                {
                    "pair": kv[f"feed{i}Pair"],
                    "price": float(kv[f"feed{i}Price"].replace("$", "").replace(",", "")),
                    "timestamp": now,
                    "stale": kv.get(f"feed{i}Status", "").lower() == "stale",
                }
                for i in range(4)
                if f"feed{i}Pair" in kv and kv.get(f"feed{i}Price")
            ]
        for val in payload.values():
            if feeds := _extract_a2ui_feeds(val):
                return feeds
    return []


class ChainlinkA2AAuthTransport(httpx.AsyncBaseTransport):
    """HTTPX transport for ADK RemoteA2aAgent supporting DCR Bearer auth and Gemini Enterprise Gateway."""

    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return await self._inner.handle_async_request(request)

        # Mode 1: Direct OAuth 2.0 DCR Bearer token against the Chainlink A2A endpoint
        token = os.environ.get("CHAINLINK_A2A_TOKEN", "").strip()
        client_id = os.environ.get("CHAINLINK_CLIENT_ID", "").strip()
        client_secret = os.environ.get("CHAINLINK_CLIENT_SECRET", "").strip()

        if not token and client_id and client_secret:
            a2a_url = os.environ.get("CHAINLINK_A2A_URL", "").rstrip("/")
            async with httpx.AsyncClient(transport=self._inner, timeout=15.0) as client:
                resp = await client.post(
                    f"{a2a_url}/oauth/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                )
                resp.raise_for_status()
                token = resp.json().get("access_token", "")

        if token:
            request.headers["Authorization"] = f"Bearer {token}"
            return await self._inner.handle_async_request(request)

        # Mode 2: Gemini Enterprise Discovery Engine A2A Gateway (Google-managed DCR)
        discovery_url = os.environ.get("CHAINLINK_DISCOVERY_ENGINE_A2A_URL", "").strip()
        if not discovery_url and (agent_path := os.environ.get("DISCOVERY_ENGINE_A2A_AGENT", "").strip()):
            discovery_url = f"https://discoveryengine.googleapis.com/v1/{agent_path}/a2a/v1/message:stream"

        rpc_body = json.loads(request.content.decode("utf-8") or "{}")
        raw_upper = json.dumps(rpc_body).upper()
        pairs = [p for p in SUPPORTED_PAIRS if p.split("/")[0] in raw_upper] or SUPPORTED_PAIRS

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        quota_project = os.environ.get("CHAINLINK_QUOTA_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))

        gateway_body = {
            "message": {
                "role": "ROLE_USER",
                "content": [{"text": json.dumps({"skill_id": "render_price_ui", "feeds": pairs[:4]})}],
                "messageId": f"req-{uuid.uuid4().hex[:8]}",
            }
        }
        async with httpx.AsyncClient(transport=self._inner, timeout=25.0) as client:
            resp = await client.post(
                discovery_url,
                headers={"Authorization": f"Bearer {creds.token}", "X-Goog-User-Project": quota_project},
                json=gateway_body,
            )
            resp.raise_for_status()
            feeds = _extract_a2ui_feeds(resp.json())

        a2a_response = {
            "jsonrpc": "2.0",
            "id": rpc_body.get("id", str(uuid.uuid4())),
            "result": {
                "kind": "message",
                "messageId": f"msg-{uuid.uuid4().hex[:8]}",
                "role": "agent",
                "parts": [{"kind": "text", "text": json.dumps({"status": "success", "feeds": feeds})}],
            },
        }
        return httpx.Response(200, json=a2a_response, request=request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def create_chainlink_a2a_httpx_client() -> httpx.AsyncClient:
    """Creates an authenticated httpx.AsyncClient for ADK RemoteA2aAgent."""
    return httpx.AsyncClient(transport=ChainlinkA2AAuthTransport(), timeout=30.0)
