# Autonomous Treasury Risk Monitor (Chainlink Data Streams + Google Cloud ADK)

An autonomous multi-agent crypto treasury risk monitor built with **Google's Agent Development Kit (ADK)**, **Google `agents-cli`**, and **Gemini 2.5 Flash**, connected directly to the [**Chainlink Data Streams Price Agent on Google Cloud Marketplace**](https://console.cloud.google.com/marketplace/product/chainlink-labs-ltd-public/chainlink-data-streams-prices-agent) over the **Agent-to-Agent (A2A)** protocol.

---

## Multi-Agent Architecture Overview

Instead of writing a custom price-fetching tool, this project uses ADK's **Multi-Agent Architecture** (`SequentialAgent` + `RemoteA2aAgent`) where the remote **Chainlink Data Streams Price Agent** participates directly as a first-class sub-agent in the pipeline:

```mermaid
flowchart LR
    subgraph UserEnv["Your Google Cloud Environment (Scaffolded via agents-cli)"]
        Scheduler["Cloud Scheduler / CLI\n(Trigger)"]
        Root["treasury_risk_monitor\n(ADK SequentialAgent)"]
        RemoteOracle["chainlink_data_streams_oracle\n(ADK RemoteA2aAgent)"]
        RiskEval["treasury_risk_evaluator\n(ADK Agent + Gemini 2.5 Flash)"]
        Slack["Slack Channel\n(#treasury-risk-alerts)"]
    end

    subgraph GCPEnt["Google Cloud Marketplace & Gemini Enterprise"]
        Discovery["Discovery Engine A2A Gateway\n(OAuth 2.0 DCR Managed)"]
        ChainlinkAgent["Chainlink Data Streams\nPrices Agent (A2A)"]
    end

    Scheduler -->|"1. Triggers check"| Root
    Root -->|"2. Step 1: Delegate A2A turn"| RemoteOracle
    RemoteOracle <-->|"A2A Protocol\n(BTC, ETH, SOL, LINK)"| Discovery
    Discovery <-->|"Bearer JWT (DCR)"| ChainlinkAgent
    Root -->|"3. Step 2: Evaluate session feeds"| RiskEval
    RiskEval -->|"4. evaluate_treasury_risk &\nsend_slack_risk_alert"| Slack
```

### How the Multi-Agent Pipeline Works
1. **`chainlink_data_streams_oracle` (`RemoteA2aAgent` in `app/agent.py`):** Resolves the Chainlink agent's `.well-known/agent.json` card (`https://<YOUR_CHAINLINK_A2A_AGENT_URL>/.well-known/agent.json`) and communicates directly with the Chainlink Data Streams Price Agent over A2A (authenticated via `app/a2a_client.py`), emitting live `BTC/USD`, `ETH/USD`, `SOL/USD`, and `LINK/USD` prices and freshness flags into the shared ADK session.
2. **`treasury_risk_evaluator` (`Agent` in `app/agent.py`):** Powered by **Gemini 2.5 Flash**, reads the verified oracle feed snapshot emitted by `chainlink_data_streams_oracle` from the conversation history, runs deterministic portfolio valuation (`evaluate_treasury_risk`), and dispatches rich Block Kit incident alerts to Slack (`send_slack_risk_alert`).
3. **`treasury_risk_monitor` (`SequentialAgent` in `app/agent.py`):** Orchestrates the end-to-end multi-agent workflow (`chainlink_price_oracle_agent` $\rightarrow$ `treasury_risk_evaluator_agent`).

---

## Repository Structure

```text
chainlink-treasury-monitor/
├── app/
│   ├── agent.py                # Multi-agent orchestration (SequentialAgent = RemoteA2aAgent + Agent)
│   ├── a2a_client.py           # Authenticated HTTPX A2A transport (DCR / Discovery Engine Gateway)
│   ├── tools.py                # Deterministic risk evaluator & Slack Block Kit alerter
│   ├── fast_api_app.py         # FastAPI A2A & ADK server entrypoint
│   └── app_utils/              # A2A card, telemetry & typing utilities
├── deployment/                 # Terraform infrastructure templates for Cloud Run
├── tests/                      # Unit, integration & ADK evaluation datasets
├── agents-cli-manifest.yaml    # Google agents-cli project manifest
├── pyproject.toml              # Project dependencies (google-adk[a2a], httpx, etc.)
├── Dockerfile                  # Cloud Run production container
├── generate_dcr.py             # Helper CLI to inspect/exchange GCP Marketplace DCR credentials
├── mock_server.py              # Local Slack webhook receiver & mock A2A server for offline dev
├── .env.example                # Environment variable template
└── BLOG.md                     # Companion technical blog post
```

---

## Step-by-Step Setup Guide

### Prerequisites

* **Google Cloud Project** with billing enabled and the **Vertex AI API** & **Discovery Engine API** enabled.
* **Python 3.11+** and [`uv`](https://docs.astral.sh/uv/) installed.
* **`gcloud` CLI** authenticated (`gcloud auth login && gcloud auth application-default login`).
* **Subscription to the Chainlink Data Streams Prices Agent** on [Google Cloud Marketplace](https://console.cloud.google.com/marketplace/product/chainlink-labs-ltd-public/chainlink-data-streams-prices-agent).

---

### Step 1: Subscribe & Register the Chainlink Agent (DCR)

1. Subscribe to the [**Chainlink Data Streams Price Agent**](https://console.cloud.google.com/marketplace/product/chainlink-labs-ltd-public/chainlink-data-streams-prices-agent) in Google Cloud Marketplace.
2. Verify that the Marketplace entitlement and Agent Card (`https://<YOUR_CHAINLINK_A2A_AGENT_URL>/`) are visible in your project:

```bash
export PROJECT_ID="your-gcp-project-id"

curl -s -X GET \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  "https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/global:fetchAgentCards" | jq .
```

3. In the **Google Cloud Console $\rightarrow$ Gemini Enterprise (Agentspace) $\rightarrow$ Agents**, register the **Chainlink Data Streams Agent** from your Marketplace procurement. Google Cloud's backend automatically executes RFC 7591 **Dynamic Client Registration (DCR)** (`POST /dcr`) against Chainlink's endpoint and stores the resulting OAuth 2.0 `client_id` and `client_secret` in Discovery Engine.
4. Inspect your active DCR authorization and Gemini Enterprise A2A Gateway endpoint using the included helper script:

```bash
python generate_dcr.py --inspect-gcp --project-id "${PROJECT_ID}"
```

---

### Step 2: Install Dependencies & Configure Environment

Clone this repository and set up the virtual environment:

```bash
git clone https://github.com/deepchenna/chainlink-treasury-monitor.git
cd chainlink-treasury-monitor

uv venv .venv
source .venv/bin/activate
uv pip install google-agents-cli "google-adk[a2a]" google-genai httpx python-dotenv fastapi uvicorn google-cloud-iam
```

Copy `.env.example` to `.env` and configure your environment variables:

```bash
cp .env.example .env
```

```dotenv
# Gemini / Vertex AI Configuration
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_GENAI_USE_VERTEXAI=TRUE

# Live Chainlink Data Streams Prices Agent (Google Cloud Marketplace)
CHAINLINK_A2A_URL=https://<YOUR_CHAINLINK_A2A_AGENT_URL>

# Gemini Enterprise A2A Gateway (Google Cloud Managed OAuth 2.0 DCR)
CHAINLINK_DISCOVERY_ENGINE_A2A_URL=https://discoveryengine.googleapis.com/v1/projects/<PROJECT_NUMBER>/locations/global/collections/default_collection/engines/<ENGINE_ID>/assistants/default_assistant/agents/<AGENT_ID>/a2a/v1/message:stream
DISCOVERY_ENGINE_A2A_AGENT=projects/<PROJECT_NUMBER>/locations/global/collections/default_collection/engines/<ENGINE_ID>/assistants/default_assistant/agents/<AGENT_ID>
CHAINLINK_QUOTA_PROJECT=your-gcp-project-id

# Slack Incoming Webhook URL (defaults to local webhook receiver for testing)
SLACK_WEBHOOK_URL=http://127.0.0.1:8099/slack-webhook
```

*(Note: This project was scaffolded using `agents-cli scaffold create chainlink-treasury-monitor --agent adk --deployment-target cloud_run --region us-central1 --prototype`.)*

---

### Step 3: Run & Test Locally

#### 1. Start the Local Slack Webhook Receiver (Optional)
If you haven't configured a production Slack Incoming Webhook yet, start the local receiver in a background terminal to capture Slack Block Kit alerts:

```bash
source .venv/bin/activate
python mock_server.py
```

#### 2. Run via `agents-cli` (One-Shot or Interactive)
From the repository root:

```bash
# One-shot multi-agent treasury risk check
agents-cli run "Run an immediate treasury risk check across BTC/USD, ETH/USD, SOL/USD, and LINK/USD."

# Or start an interactive multi-turn chat session
agents-cli run
```

#### 3. Test Visually in the ADK Developer Web UI
Launch the interactive ADK Web UI to inspect the multi-agent graph (`chainlink_data_streams_oracle` $\rightarrow$ `treasury_risk_evaluator`), A2A calls, and tool traces in your browser:

```bash
adk web --port 8080
```
Open **`http://localhost:8080`**, select **`app`** from the top-left dropdown, and send:
> *"Run an immediate treasury risk check across BTC/USD, ETH/USD, SOL/USD, and LINK/USD."*

#### 4. Inspect Captured Slack Block Kit Alerts
If using the local webhook receiver (`mock_server.py`), inspect the delivered Slack alert payloads:

```bash
curl -s http://127.0.0.1:8099/slack-alerts | python3 -m json.tool
```

---

### Step 4: Deploy to Cloud Run & Schedule with Cloud Scheduler

Deploy the agent to **Google Cloud Run** using `agents-cli`:

```bash
agents-cli deploy --env prod --project "${PROJECT_ID}" --region us-central1
```

To run autonomous treasury checks every 5 minutes, create a **Cloud Scheduler** job that triggers your Cloud Run service:

```bash
gcloud scheduler jobs create http treasury-risk-monitor-cron \
  --location="us-central1" \
  --schedule="*/5 * * * *" \
  --uri="https://treasury-risk-monitor-<HASH>-uc.a.run.app/run" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"app_name":"app","user_id":"scheduler","session_id":"cron","new_message":{"role":"user","parts":[{"text":"Run an immediate treasury risk check across BTC/USD, ETH/USD, SOL/USD, and LINK/USD."}]}}' \
  --oidc-service-account-email="treasury-monitor-sa@${PROJECT_ID}.iam.gserviceaccount.com"
```

---

## License

Apache 2.0
