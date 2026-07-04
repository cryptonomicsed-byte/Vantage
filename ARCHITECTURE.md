# Vantage Architecture — External Systems

This document describes systems that Vantage depends on or integrates with
that live outside this repository. They are not built, deployed, or versioned
as part of Vantage itself.

---

## Trading Signal Daemons

These are standalone Python scripts that poll external data sources and POST
results into Vantage's trading signal ingestion endpoint. They are started
manually on the host machine and run as background processes.

| Daemon | Purpose | Feeds |
|--------|---------|-------|
| vantage_predictor | Multi-indicator technical analysis across 30 tokens | trading signals |
| trading_agents | Multi-agent LLM debate producing structured trade decisions | trading signals |
| unified_ingester | Aggregates 14 free crypto APIs at tiered polling rates | trading signals |
| signal_aggregator | NLP sentiment on headlines + on-chain whale detection + price anomaly scanner | trading signals |
| alpha_sources | FinBERT transformer model for financial sentiment + DEX swap data + geopolitical event feed | trading signals |
| advanced_analytics | Portfolio-level vectorbt backtesting + on-chain analytics + deep article scraping | trading signals |

**Interface:** Each daemon POSTs structured signal payloads to Vantage's
trading signal ingestion endpoint. Daemon health is not monitored by Vantage —
they are independent processes that can fail without affecting the platform.

---

## Security Daemons

| Daemon | Purpose | Trigger |
|--------|---------|---------|
| stix_ingester | Pulls threat intelligence from public feeds, curates known exploit database, emits STIX 2.1 bundles | Scheduled polling |
| stix_scanner | Scans hosted repositories for 15 vulnerability patterns (secrets, unsafe eval, SQL injection, exposed keys, etc.) | Scheduled polling |
| stix_webhook | Listens for repository push events and triggers immediate security scans on changed files | HTTP webhook |

**Interface:** Security findings are posted to Vantage as threat signals and,
when triggered by repository events, as pull-request comments on the hosting
service.

---

## Pipeline Orchestration

Manually-invoked scripts that chain multiple systems together for autonomous
workflows.

| Script | Purpose |
|--------|---------|
| pipeline conductor | Accepts a target, dispatches parallel SEO and security scans, collects reports, pushes artifacts to repositories, updates memory context, and publishes results |

**Interface:** Invoked via CLI. Communicates with Vantage through its REST
API. Not called by Vantage — it calls into Vantage.

---

## External Services (Docker containers)

Dockerized services that Vantage's backend or frontend communicates with over
the network.

| Service | Purpose |
|---------|---------|
| Git server | Hosts agent repositories; triggers webhooks on push |
| Security sandbox | Isolated environment for running untrusted code and security tooling |
| Video renderer | Generates MP4 video from structured scene descriptions and publishes thumbnails |
| RPC proxy | JSON-RPC gateway for blockchain node access |

---

## Host-Level Configuration

Environment variables and configuration files that Vantage and its daemons
expect to find on the host filesystem. These are not in version control.

- Agent authentication key for the platform API
- Third-party API credentials for LLM providers and data services
- Host-level environment file with service tokens

---

## Integration Notes

**Daemons that are candidates for first-class integration:**

Any daemon in this document that an agent should be able to invoke directly
(rather than only running as a background process on a specific host) is a
candidate for elevation into Vantage's job conductor or MCP tool system.
If a daemon becomes a Vantage feature, it should be submitted as a feature
request with the actual working script, and integrated properly — not
documented as external.
