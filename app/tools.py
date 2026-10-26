import base64
import json
import os
import time
import uuid
import httpx


# Define our DAO / Enterprise Treasury Holdings and Risk Thresholds
TREASURY_CONFIG = {
    "min_total_usd_value": 10_000_000.00,  # Breach if total portfolio < $10M
    "positions": {
        "BTC/USD": {"units": 85.0, "price_floor": 62_000.00},
        "ETH/USD": {"units": 1_200.0, "price_floor": 2_800.00},
        "SOL/USD": {"units": 8_500.0, "price_floor": 135.00},
        "LINK/USD": {"units": 45_000.0, "price_floor": 12.50},
    },
}

PLACEHOLDER_TOKENS = {"", "test-dcr-bearer-jwt-token", "your-dcr-bearer-jwt-token"}


def _fetch_via_discovery_engine_a2a(
    discovery_url: str, quota_project: str, pairs: list[str]
) -> dict:
    """Invokes the DCR-registered Chainlink A2A Agent via Discovery Engine A2A Gateway."""
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

    with httpx.Client(timeout=25.0) as client:
        resp = client.post(discovery_url, headers=headers, json=body)
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
                            raw_price = kv.get(f"feed{idx}Price", "").replace("$", "").replace(",", "").strip()
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
        "source": "Chainlink Data Streams A2A Agent (GCP Marketplace / DCR Gateway)",
        "agent_endpoint": os.environ.get("CHAINLINK_A2A_URL", ""),
        "dcr_client_id": os.environ.get("CHAINLINK_CLIENT_ID", ""),
        "updated_label": updated_label,
        "feeds": feeds,
    }


def fetch_chainlink_oracle_prices(pairs: list[str]) -> dict:
    """Queries the Chainlink Data Streams Price Agent via A2A JSON-RPC or DCR Gateway.

    Args:
        pairs: List of asset pairs to query, e.g. ["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD"].

    Returns:
        dict containing the verified Chainlink Data Streams price feeds and timestamps.
    """
    a2a_url = os.environ.get("CHAINLINK_A2A_URL", "").rstrip("/")
    a2a_token = os.environ.get("CHAINLINK_A2A_TOKEN", "").strip()
    client_id = os.environ.get("CHAINLINK_CLIENT_ID", "").strip()
    client_secret = os.environ.get("CHAINLINK_CLIENT_SECRET", "").strip()
    discovery_url = os.environ.get("CHAINLINK_DISCOVERY_ENGINE_A2A_URL", "").strip()
    quota_project = os.environ.get(
        "CHAINLINK_QUOTA_PROJECT",
        os.environ.get("GOOGLE_CLOUD_PROJECT", "your-gcp-project-id"),
    ).strip()

    # If no direct Bearer token or client_secret is present locally, route through the
    # DCR-authorized Gemini Enterprise / Discovery Engine A2A Gateway endpoint.
    if a2a_token in PLACEHOLDER_TOKENS and not (client_id and client_secret) and discovery_url:
        return _fetch_via_discovery_engine_a2a(discovery_url, quota_project, pairs)

    # Standard A2A JSON-RPC 2.0 SendMessage payload targeting the 'stream_prices' skill
    payload = {
        "jsonrpc": "2.0",
        "id": f"oracle-req-{uuid.uuid4().hex[:8]}",
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "user",
                "parts": [
                    {
                        "type": "data",
                        "data": {
                            "skill_id": "stream_prices",
                            "feeds": pairs[:4],  # V1 supports up to 4 feeds per call
                        },
                    }
                ],
            }
        },
    }

    with httpx.Client(timeout=10.0) as client:
        if a2a_token in PLACEHOLDER_TOKENS and client_id and client_secret:
            token_resp = client.post(
                f"{a2a_url}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            token_resp.raise_for_status()
            a2a_token = token_resp.json().get("access_token", "")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {a2a_token}",
        }
        response = client.post(f"{a2a_url}/", json=payload, headers=headers)
        response.raise_for_status()
        rpc_data = response.json()

    # Extract the feeds array from the A2A response artifact
    result = rpc_data.get("result", {})
    artifacts = result.get("artifacts") or result.get("task", {}).get("artifacts", [])
    for artifact in artifacts:
        data = artifact.get("data") or {}
        if "feeds" in data:
            return {"status": "success", "source": "Chainlink Data Streams A2A Agent", "feeds": data["feeds"]}

    return {"status": "success", "raw_a2a_result": result}


def evaluate_treasury_risk(feeds: list[dict]) -> dict:
    """Evaluates treasury portfolio value and checks for risk threshold breaches.

    Args:
        feeds: List of feed objects returned by fetch_chainlink_oracle_prices,
               each containing 'pair', 'price', 'timestamp', and 'stale'.

    Returns:
        dict with portfolio valuation, asset breakdown, and any active risk breaches.
    """
    total_usd_value = 0.0
    asset_breakdown = []
    breaches = []

    price_map = {item["pair"]: item for item in feeds}

    for pair, config in TREASURY_CONFIG["positions"].items():
        feed = price_map.get(pair)
        if not feed or feed.get("price") is None:
            breaches.append(f"MISSING_FEED: No valid price returned for {pair}.")
            continue

        if feed.get("stale", False):
            breaches.append(
                f"STALE_ORACLE_DATA: {pair} tick at timestamp {feed.get('timestamp')} is marked stale (>10s old)."
            )

        spot_price = float(feed["price"])
        units = config["units"]
        position_usd = spot_price * units
        total_usd_value += position_usd

        floor = config["price_floor"]
        floor_breached = spot_price < floor
        if floor_breached:
            drawdown_pct = ((floor - spot_price) / floor) * 100
            breaches.append(
                f"PRICE_FLOOR_BREACH: {pair} spot price (${spot_price:,.2f}) breached risk floor "
                f"(${floor:,.2f}) by -{drawdown_pct:.2f}%."
            )

        asset_breakdown.append({
            "pair": pair,
            "units": units,
            "spot_price": spot_price,
            "price_floor": floor,
            "position_usd": round(position_usd, 2),
            "timestamp": feed.get("timestamp"),
            "stale": feed.get("stale", False),
            "floor_breached": floor_breached,
        })

    min_total = TREASURY_CONFIG["min_total_usd_value"]
    if total_usd_value < min_total:
        shortfall = min_total - total_usd_value
        breaches.append(
            f"PORTFOLIO_VALUATION_BREACH: Total treasury value (${total_usd_value:,.2f}) dropped below "
            f"minimum reserve requirement (${min_total:,.2f}) by ${shortfall:,.2f}."
        )

    return {
        "status": "BREACH_DETECTED" if breaches else "HEALTHY",
        "evaluated_at_unix": int(time.time()),
        "total_portfolio_usd": round(total_usd_value, 2),
        "min_required_usd": min_total,
        "breach_count": len(breaches),
        "breaches": breaches,
        "positions": asset_breakdown,
    }


def send_slack_risk_alert(
    severity: str,
    headline: str,
    total_portfolio_usd: float,
    breach_summary: str,
    recommended_actions: str,
) -> dict:
    """Posts a rich Block Kit treasury risk alert to the configured Slack webhook.

    Args:
        severity: Alert severity level, e.g. "CRITICAL" or "WARNING".
        headline: Short one-line summary of the treasury breach.
        total_portfolio_usd: Current total USD valuation of the treasury.
        breach_summary: Bullet list of breached thresholds with exact oracle prices.
        recommended_actions: Concrete mitigation steps recommended by the agent.

    Returns:
        dict indicating whether the Slack notification succeeded.
    """
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return {"status": "skipped", "reason": "SLACK_WEBHOOK_URL not configured"}

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🚨 [{severity}] Treasury Risk Threshold Breached",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": "*Oracle Source:*\nChainlink Data Streams (A2A)"},
                {"type": "mrkdwn", "text": f"*Total Treasury Value:*\n`${total_portfolio_usd:,.2f} USD`"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Summary:*\n{headline}\n\n*Breached Thresholds:*\n{breach_summary}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Recommended Actions (Gemini Risk Analysis):*\n{recommended_actions}"},
        },
    ]

    with httpx.Client(timeout=10.0) as client:
        resp = client.post(webhook_url, json={"blocks": blocks})
        resp.raise_for_status()

    return {"status": "sent", "http_status": resp.status_code}
