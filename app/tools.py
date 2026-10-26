import os
import time
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


def evaluate_treasury_risk(feeds: list[dict]) -> dict:
    """Evaluates treasury portfolio value and checks for risk threshold breaches.

    Args:
        feeds: List of feed objects returned by the Chainlink Data Streams A2A Agent,
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
