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
"""
You can add your unit tests here.
This is where you test your business logic, including agent functionality,
data processing, and other core components of your application.
"""


from google.adk.agents import Agent, SequentialAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

from app.agent import (
    chainlink_price_oracle_agent,
    root_agent,
    treasury_risk_evaluator_agent,
)
from app.tools import evaluate_treasury_risk


def test_multi_agent_architecture() -> None:
    """Verifies that root_agent is a SequentialAgent with RemoteA2aAgent and Risk Evaluator sub-agents."""
    assert isinstance(root_agent, SequentialAgent)
    assert len(root_agent.sub_agents) == 2
    assert isinstance(chainlink_price_oracle_agent, RemoteA2aAgent)
    assert isinstance(treasury_risk_evaluator_agent, Agent)
    assert root_agent.sub_agents[0].name == "chainlink_data_streams_oracle"
    assert root_agent.sub_agents[1].name == "treasury_risk_evaluator"


def test_evaluate_treasury_risk_breach_and_healthy() -> None:
    """Verifies deterministic treasury valuation and risk floor / staleness breach checks."""
    healthy_feeds = [
        {"pair": "BTC/USD", "price": 85000.0, "timestamp": 1793009069, "stale": False},
        {"pair": "ETH/USD", "price": 3100.0, "timestamp": 1793009069, "stale": False},
        {"pair": "SOL/USD", "price": 150.0, "timestamp": 1793009069, "stale": False},
        {"pair": "LINK/USD", "price": 15.0, "timestamp": 1793009069, "stale": False},
    ]
    res_healthy = evaluate_treasury_risk(healthy_feeds)
    assert res_healthy["status"] == "HEALTHY"
    assert res_healthy["breach_count"] == 0

    breach_feeds = [
        {"pair": "BTC/USD", "price": 81477.89, "timestamp": 1793009069, "stale": True},
        {"pair": "ETH/USD", "price": 2392.73, "timestamp": 1793009069, "stale": False},
        {"pair": "SOL/USD", "price": 103.83, "timestamp": 1793009069, "stale": False},
        {"pair": "LINK/USD", "price": 12.07, "timestamp": 1793009069, "stale": False},
    ]
    res_breach = evaluate_treasury_risk(breach_feeds)
    assert res_breach["status"] == "BREACH_DETECTED"
    assert res_breach["breach_count"] >= 4
