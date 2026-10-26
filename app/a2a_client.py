import base64
import json
import os
import time
import uuid
import httpx

SUPPORTED_PAIRS = ["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD"]
PLACEHOLDER_TOKENS = {"", "test-dcr-bearer-jwt-token", "your-dcr-bearer-jwt-token"}


def _extract_requested_pairs(rpc_body: dict) -> list[str]:
    """Extracts requested Chainlink feed pairs from an outgoing A2A SendMessage payload."""
    raw_text = json.dumps(rpc_body).upper()
    matched = [pair for pair in SUPPORTED_PAIRS if pair in raw_text or pair.split("/")[0] in raw_text]
    return matched if matched else list(SUPPORTED_PAIRS)


async def _invoke_discovery_engine_a2a_gateway(
    transport: httpx.AsyncBaseTransport,
    discovery_url: str,
    quota_project: str,
    pairs: list[str],
) -> dict:
    """Bridges an A2A SendMessage request through the DCR-authorized Discovery Engine A2A Gateway."""
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())

    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
    }
    if quota_project:
        headers["X-Goog-User-Project"] = quota_project

    body = {
        "message": {
            "role": "ROLE_USER",
            "content": [
                {
                    "text": json.dumps(
                        {
                            "skill_id": "render_price_ui",
                            "feeds": pairs[:4],
                        }
                    )
                }
            ],
            "messageId": f"oracle-req-{uuid.uuid4().hex[:8]}",
        }
    }

    async with httpx.AsyncClient(transport=transport, timeout=25.0) as client:
        resp = await client.post(discovery_url, headers=headers, json=body)
        resp.raise_for_status()
        stream_chunks = resp.json()

    now_ts = int(time.time())
    feeds = []
    updated_label = None

    for chunk in stream_chunks if isinstance(stream_chunks, list) else [stream_chunks]:
        replies = chunk.get("replies", []) + (
            chunk.get("message", {})
            .get("metadata", {})
            .get("answer", {})
            .get("replies", [])
        )
        for reply in replies:
            content_obj = reply.get("groundedContent", {}).get("content", {})
            candidate_parts = list(content_obj.get("parts", []))
            if "inlineData" in content_obj:
                candidate_parts.append({"inlineData": content_obj["inlineData"]})
            for part in candidate_parts:
                inline = part.get("inlineData", {})
                if inline.get("mimeType") == "application/json+a2ui" and inline.get("data"):
                    decoded = json.loads(base64.b64decode(inline["data"]).decode("utf-8"))
                    dmu = decoded.get("dataModelUpdate", {})
                    contents = dmu.get("contents", [])
                    if contents:
                        kv = {
                            item["key"]: item.get("valueString", "")
                            for item in contents
                            if "key" in item
                        }
                        updated_label = kv.get("updatedLabel")
                        idx = 0
                        while f"feed{idx}Pair" in kv:
                            pair_name = kv[f"feed{idx}Pair"]
                            raw_price = (
                                kv.get(f"feed{idx}Price", "")
                                .replace("$", "")
                                .replace(",", "")
                                .strip()
                            )
                            raw_status = kv.get(f"feed{idx}Status", "").strip().lower()
                            if pair_name and raw_price:
                                feeds.append(
                                    {
                                        "pair": pair_name,
                                        "price": float(raw_price),
                                        "timestamp": now_ts,
                                        "stale": raw_status == "stale",
                                    }
                                )
                            idx += 1

    return {
        "status": "success",
        "source": "Chainlink Data Streams A2A Agent",
        "updated_label": updated_label,
        "feeds": feeds,
    }


def _build_a2a_rpc_response(rpc_body: dict, oracle_data: dict) -> dict:
    """Formats the JSON-RPC response for either A2A v0.3 (message/send) or A2A v1.0 (SendMessage)."""
    rpc_id = rpc_body.get("id", str(uuid.uuid4()))
    msg_id = f"chainlink-msg-{uuid.uuid4().hex[:8]}"
    text_payload = json.dumps(oracle_data)

    if rpc_body.get("method") == "SendMessage":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "message": {
                    "messageId": msg_id,
                    "role": "ROLE_AGENT",
                    "parts": [{"text": text_payload}],
                }
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {
            "kind": "message",
            "messageId": msg_id,
            "role": "agent",
            "parts": [{"kind": "text", "text": text_payload}],
        },
    }


class ChainlinkA2AAuthTransport(httpx.AsyncBaseTransport):
    """Custom HTTPX transport for ADK RemoteA2aAgent that handles Chainlink DCR OAuth & A2A framing."""

    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Allow Agent Card discovery (/.well-known/agent.json) to pass through directly
        if request.method == "GET":
            return await self._inner.handle_async_request(request)

        raw_body = request.content.decode("utf-8") if request.content else "{}"
        try:
            rpc_body = json.loads(raw_body)
        except json.JSONDecodeError:
            return await self._inner.handle_async_request(request)

        rpc_id = rpc_body.get("id", str(uuid.uuid4()))
        pairs = _extract_requested_pairs(rpc_body)

        a2a_url = os.environ.get("CHAINLINK_A2A_URL", "").rstrip("/")
        a2a_token = os.environ.get("CHAINLINK_A2A_TOKEN", "").strip()
        client_id = os.environ.get("CHAINLINK_CLIENT_ID", "").strip()
        client_secret = os.environ.get("CHAINLINK_CLIENT_SECRET", "").strip()
        discovery_url = os.environ.get("CHAINLINK_DISCOVERY_ENGINE_A2A_URL", "").strip()
        if not discovery_url:
            discovery_agent = os.environ.get("DISCOVERY_ENGINE_A2A_AGENT", "").strip()
            if discovery_agent:
                discovery_url = (
                    f"https://discoveryengine.googleapis.com/v1/{discovery_agent}/a2a/v1/message:stream"
                )
        quota_project = os.environ.get(
            "CHAINLINK_QUOTA_PROJECT",
            os.environ.get("GOOGLE_CLOUD_PROJECT", "your-gcp-project-id"),
        ).strip()

        # Route via DCR-authorized Discovery Engine A2A Gateway if configured
        if (
            a2a_token in PLACEHOLDER_TOKENS
            and not (client_id and client_secret)
            and discovery_url
        ):
            oracle_data = await _invoke_discovery_engine_a2a_gateway(
                self._inner, discovery_url, quota_project, pairs
            )
            a2a_rpc_response = _build_a2a_rpc_response(rpc_body, oracle_data)
            return httpx.Response(
                status_code=200,
                headers={"Content-Type": "application/json"},
                content=json.dumps(a2a_rpc_response).encode("utf-8"),
                request=request,
            )

        # Otherwise authenticate directly against the Chainlink A2A endpoint using DCR credentials
        async with httpx.AsyncClient(transport=self._inner, timeout=15.0) as client:
            if a2a_token in PLACEHOLDER_TOKENS and client_id and client_secret:
                token_resp = await client.post(
                    f"{a2a_url}/oauth/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                )
                token_resp.raise_for_status()
                a2a_token = token_resp.json().get("access_token", "")

            direct_payload = {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [
                            {
                                "type": "data",
                                "data": {
                                    "skill_id": "stream_prices",
                                    "feeds": pairs[:4],
                                },
                            }
                        ],
                    }
                },
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {a2a_token}",
            }
            resp = await client.post(f"{a2a_url}/", json=direct_payload, headers=headers)
            resp.raise_for_status()
            rpc_data = resp.json()

        result = rpc_data.get("result", {})
        artifacts = result.get("artifacts") or result.get("task", {}).get("artifacts", [])
        oracle_data = {"status": "success", "source": "Chainlink Data Streams A2A Agent", "feeds": []}
        for artifact in artifacts:
            data = artifact.get("data") or {}
            if "feeds" in data:
                oracle_data["feeds"] = data["feeds"]
                break

        a2a_rpc_response = _build_a2a_rpc_response(rpc_body, oracle_data)
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(a2a_rpc_response).encode("utf-8"),
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


def create_chainlink_a2a_httpx_client() -> httpx.AsyncClient:
    """Creates an authenticated httpx.AsyncClient for ADK RemoteA2aAgent."""
    return httpx.AsyncClient(transport=ChainlinkA2AAuthTransport(), timeout=30.0)
