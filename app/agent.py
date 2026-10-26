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

from google.adk.agents import Agent, SequentialAgent
from google.adk.agents.remote_a2a_agent import AGENT_CARD_WELL_KNOWN_PATH, RemoteA2aAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from .a2a_client import create_chainlink_a2a_httpx_client
from .tools import (
    evaluate_treasury_risk,
    send_slack_risk_alert,
)

MODEL = "gemini-2.5-flash"

chainlink_a2a_url = os.environ.get(
    "CHAINLINK_A2A_URL", "https://<YOUR_CHAINLINK_A2A_AGENT_URL>"
).rstrip("/")

# 1. Remote A2A Agent: Official Chainlink Data Streams Price Agent
chainlink_price_oracle_agent = RemoteA2aAgent(
    name="chainlink_data_streams_oracle",
    description=(
        "Official Chainlink Data Streams A2A Agent providing low-latency spot prices "
        "for BTC/USD, ETH/USD, SOL/USD, and LINK/USD."
    ),
    agent_card=f"{chainlink_a2a_url}{AGENT_CARD_WELL_KNOWN_PATH}",
    httpx_client=create_chainlink_a2a_httpx_client(),
)

RISK_EVALUATOR_INSTRUCTION = """
You are an Autonomous Institutional Treasury Risk Evaluator powered by Google Gemini and Chainlink Data Streams.

The preceding agent in this multi-agent pipeline (`chainlink_data_streams_oracle`) has just queried the Chainlink Data Streams A2A Agent and emitted a JSON snapshot containing the `feeds` list (`pair`, `price`, `timestamp`, `stale`) for BTC/USD, ETH/USD, SOL/USD, and LINK/USD.

Your responsibilities:
1. Extract the `feeds` list returned by `chainlink_data_streams_oracle` from the conversation history and pass it directly into `evaluate_treasury_risk` to compute the current USD valuation of all treasury positions and verify whether any asset price floor, total reserve floor, or oracle staleness invariant (`stale == true`) has been breached.
2. If `status == "BREACH_DETECTED"`:
   - Synthesize a concise, quantitative risk analysis explaining which positions triggered the breach, their percentage drawdown relative to the risk floor, and immediate mitigation steps (e.g., rebalancing USDC reserves, posting additional ETH collateral, or pausing automated deployments if oracle data is stale).
   - Immediately call `send_slack_risk_alert` to notify the treasury operations channel.
3. If `status == "HEALTHY"`, report the verified portfolio valuation and confirm that no Slack alert was required.

Rules:
- Never guess, fabricate, or extrapolate cryptocurrency prices. Always rely strictly on the Chainlink Data Streams A2A Agent output in the conversation history.
- Always cite the exact price and unix timestamp returned by `chainlink_data_streams_oracle` for every asset.
"""

# 2. Local ADK Agent: Treasury Risk Evaluator & Slack Incident Responder
treasury_risk_evaluator_agent = Agent(
    name="treasury_risk_evaluator",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description="Evaluates crypto treasury reserves against risk thresholds using Chainlink Data Streams prices and sends Slack alerts on breaches.",
    instruction=RISK_EVALUATOR_INSTRUCTION,
    tools=[
        evaluate_treasury_risk,
        send_slack_risk_alert,
    ],
)

# 3. Multi-Agent Pipeline: Chainlink Data Streams A2A Agent -> Treasury Risk Evaluator Agent
root_agent = SequentialAgent(
    name="treasury_risk_monitor",
    description="Autonomous multi-agent treasury risk monitor that queries the Chainlink Data Streams A2A Agent and evaluates portfolio risk thresholds.",
    sub_agents=[
        chainlink_price_oracle_agent,
        treasury_risk_evaluator_agent,
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
