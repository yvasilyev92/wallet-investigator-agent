# Wallet Investigator

Ethereum-mainnet wallet risk tool: map a wallet's counterparties, screen them against OFAC and Etherscan labels, score simple heuristics, and click a node for an agent-written case file.

Ethereum mainnet only.

## What it does

You paste a `0x` address and click **Investigate**. The app does **not** ask an LLM to decide risk. It:

1. Fetches that wallet's Ethereum transaction history from the Etherscan V2 API.
2. Builds an in-memory graph of connected wallets (at most **2 hops**), pruning dust, high-activity hubs, and extra counterparties so the picture stays readable.
3. Screens every node against a local OFAC SDN extract (`data/ofac_sanctions.json`) and, when the API allows, Etherscan nametag/phish-hack labels.
4. Scores each wallet with three additive heuristic rules (see [Heuristic score](#heuristic-score)). Each rule records **why** it fired.
5. Draws an interactive graph (node color = risk bucket, size = tx volume). Click a node to see the score breakdown and a **case file**.

The case file is the only place a language model runs, and only for the wallet you clicked — never for every node in the graph.

## Stack

- **uv** — Python env and dependency management
- **Pydantic** — shared data models (wallets, txs, scores, investigation result)
- **httpx** (async) — Etherscan V2 API calls
- **NetworkX** — in-memory 2-hop wallet graph
- **Dash** + **dash-cytoscape** — UI and interactive risk graph
- **LangChain** (`create_agent`, tools) — click-only case-file agent
- **langchain-openai** / **OpenAI** (`gpt-4.1-mini`) — the agent’s chat model
- **Etherscan V2** — Ethereum mainnet transaction history and nametag labels
- **OFAC SDN extract** — local JSON of sanctioned `0x` addresses + metadata

## How it uses agentic AI

Investigation is deterministic. Agentic AI is a **bounded tool loop on click**, implemented with LangChain `create_agent` in `src/wallet_investigator/agent.py`.

When you tap a node, the app starts an agent whose job is: write a compliance-style markdown briefing for **that one address**. The model does not fetch the chain itself and does not invent a new score. It may call local tools that read facts already computed:

| Tool | What it returns |
|---|---|
| `get_score` | Frozen heuristic score, bucket, and rule reasons |
| `get_ofac_record` | SDN metadata if the address is in the local OFAC extract (name, aliases, programs, legal authorities, list date, secondary-sanctions language, related entities) |
| `get_neighbor_context` | 1-hop counterparties already in the graph, with their scores and OFAC names |
| `lookup_program` | Plain-English gloss of an OFAC program code (e.g. `DPRK3`, `ILLICIT-DRUGS-EO14059`) from a static glossary — no web search |
| `get_labels` | Screening pills already attached to the wallet (OFAC + Etherscan) |

The agent is instructed to quote the given score, never re-decide risk, never browse the web, and never invent listings or counterparties the tools did not return. It then writes a multi-section case file (verdict, identity, why this score, network context, labels, caveats).

If `OPENAI_API_KEY` is missing or the agent fails, the same sections are filled from the graph and OFAC JSON without a model. Repeat clicks on the same node are cached.

That split is intentional: **heuristics own the number; the agent owns the narrative**, and only when an analyst asks about a specific wallet.

## Heuristic score

The number on a node is **not** a probability, a model output, or an official risk rating. It is a 0–100 total from three yes/no rules in `src/wallet_investigator/scoring.py`. Fired rules add their points; the sum is capped at 100. Node color is the bucket of that total.

| Bucket | Score | Typical meaning |
|---|---|---|
| Low | 0–29 | No OFAC proximity in this graph. Pass-through alone (25) still lands here. |
| Medium | 30–69 | A sanctioned address is two hops away (40), optionally plus pass-through (65). |
| High | 70–100 | Direct OFAC hit (80). Adding other rules only changes the total up to the cap. |

| Rule | Points | When it fires |
|---|---|---|
| Direct sanctioned | 80 | The wallet **is** on the local OFAC extract, **or** it transacted with an address that is. |
| Two-hop sanctioned | 40 | A sanctioned address is exactly two hops away in the investigation graph, and is not also a direct counterparty (so it does not double-count the direct rule). |
| Pass-through | 25 | At least 90% of incoming ETH value also left, and at least 90% of incoming value had an outbound tx within **1 hour**. Coarse mixer / peel-chain proxy, not a full flow trace. |

Points and cutoffs live in `src/wallet_investigator/config.py` (`RULE_*_POINTS`, `BUCKET_*_MIN`, `PASSTHROUGH_*`). They are starting values, not calibrated.

What the score does **not** mean:

- It does not use token transfers, internal txs, or anything beyond the capped Etherscan `txlist` used to build the graph.
- Two-hop exposure is only as complete as the graph (dust prune, hub cutoff, neighbor cap, 2-hop limit). Missing edges can hide a path; extra edges cannot invent an OFAC listing.
- A high score is “this investigation found a strong OFAC or pass-through signal,” not “this wallet is sanctioned” unless the direct rule says the address itself is listed.

## Setup

Install [uv](https://docs.astral.sh/uv/) if you do not have it, then from this directory:

```bash
uv sync
cp .env.example .env
```

Edit `.env` and set:

- `ETHERSCAN_API_KEY` — required for transaction fetching
- `OPENAI_API_KEY` — optional; if missing, the side panel still builds a structured case file from local OFAC/graph data without an LLM

## Run

```bash
uv run python -m wallet_investigator
```

Equivalent: `uv run wallet-investigator`

Then open http://127.0.0.1:8050

## How it works

1. Validates the address (`0x` + 40 hex characters) before any network calls.
2. Pulls normal transactions from the Etherscan V2 API (`chainid=1`).
3. Builds a NetworkX graph out to 2 hops, pruning dust, high-activity hubs, and extra counterparties.
4. Checks wallets against a local OFAC address list and Etherscan nametag labels.
5. Scores each wallet with the three additive heuristics above (direct OFAC, two-hop OFAC, pass-through).
6. Renders a Dash / dash-cytoscape graph. Click a node for the score breakdown and an agent-written case file (local tools only; the score is never re-decided).

## Notes

- `MAX_TX_PER_WALLET` and the prune/score thresholds live in `src/wallet_investigator/config.py` and are marked with TODOs to tune.
- `data/ofac_sanctions.json` is OFAC `0x` wallets (ETH plus USDC/USDT-on-ETH, etc.) extracted from `SDN_ENHANCED.XML`. Refresh with:

```bash
uv run python scripts/extract_ofac_eth.py --xml sdn_enhanced.xml --out data/ofac_sanctions.json
```
- Etherscan nametag lookup is per-address (no clean bulk API on the free tier) and is cached in memory.
