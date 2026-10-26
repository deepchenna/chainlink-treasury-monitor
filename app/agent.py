# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
from dotenv import load_dotenv

load_dotenv(override=True)
if os.environ.get("GOOGLE_API_KEY"):
    os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
    os.environ.pop("GOOGLE_CLOUD_LOCATION", None)

from google.adk.agents import Agent
from google.adk.agents.remote_a2a_agent import AGENT_CARD_WELL_KNOWN_PATH, RemoteA2aAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from .tools import (
    evaluate_treasury_risk,
    fetch_chainlink_oracle_prices,
    send_slack_risk_alert,
)

MODEL = "gemini-2.5-flash"

# Optional: Native ADK RemoteA2aAgent for conversational A2A delegation
chainlink_a2a_url = os.environ.get(
    "CHAINLINK_A2A_URL", "https://<YOUR_CHAINLINK_A2A_AGENT_URL>"
).rstrip("/")
chainlink_price_oracle_subagent = RemoteA2aAgent(
    name="chainlink_data_streams_oracle",
    description=(
        "Official Chainlink Data Streams A2A Agent providing low-latency spot prices "
        "and charts for BTC/USD, ETH/USD, SOL/USD, and LINK/USD."
    ),
    agent_card=f"{chainlink_a2a_url}{AGENT_CARD_WELL_KNOWN_PATH}",
)

RISK_MONITOR_INSTRUCTION = """
You are an Autonomous Institutional Treasury Risk Monitor powered by Google Gemini and Chainlink Data Streams.

When asked to run a treasury risk check or evaluate portfolio exposure:
1. Call `fetch_chainlink_oracle_prices` for ["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD"] to retrieve live, sub-second price snapshots from the Chainlink Data Streams A2A Agent.
2. Pass the returned `feeds` list directly into `evaluate_treasury_risk` to compute the current USD valuation of all treasury positions and verify whether any asset price floor, total reserve floor, or oracle staleness invariant (`stale == true`) has been breached.
3. If `status == "BREACH_DETECTED"`:
   - Synthesize a concise, quantitative risk analysis explaining which positions triggered the breach, their percentage drawdown relative to the risk floor, and immediate mitigation steps (e.g., rebalancing USDC reserves, posting additional ETH collateral, or pausing automated deployments if oracle data is stale).
   - Immediately call `send_slack_risk_alert` to notify the treasury operations channel.
4. If `status == "HEALTHY"`, report the verified portfolio valuation and confirm that no Slack alert was required.

Rules:
- Never guess, fabricate, or extrapolate cryptocurrency prices. Always rely strictly on the Chainlink Data Streams A2A Agent output.
- Always cite the exact price and unix timestamp returned by Chainlink for every asset.
"""

root_agent = Agent(
    name="treasury_risk_monitor",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description="Monitors crypto treasury reserves using Chainlink Data Streams and sends Slack alerts on risk breaches.",
    instruction=RISK_MONITOR_INSTRUCTION,
    tools=[
        fetch_chainlink_oracle_prices,
        evaluate_treasury_risk,
        send_slack_risk_alert,
    ],
    sub_agents=[chainlink_price_oracle_subagent],
)

app = App(
    root_agent=root_agent,
    name="app",
)
