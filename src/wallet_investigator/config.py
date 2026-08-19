"""Tunable constants and environment configuration.

Thresholds below are starting points for v1. Each TODO marks a value that
should be calibrated against real investigations.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"
ETHEREUM_CHAIN_ID = 1

# Free-tier Etherscan V2 limit is 3 calls/sec.
ETHERSCAN_MIN_INTERVAL_SECONDS = 0.4
ETHERSCAN_MAX_RETRIES = 5

# TODO: tune. Whale wallets can have millions of txs; txlist returns at most
# 10,000 records per query. Cap keeps investigations from hanging.
MAX_TX_PER_WALLET = 100
TXLIST_PAGE_SIZE = 100

# TODO: tune. Skip tiny ETH transfers that are usually spam/dust.
DUST_THRESHOLD_WEI = 10**14  # 0.0001 ETH

# TODO: tune. Wallets with this many fetched txs are treated as exchanges/
# services: keep their edges, but do not BFS through them.
HIGH_TX_COUNT_THRESHOLD = 200

# TODO: tune. Even a normal wallet can have hundreds of counterparties;
# only expand the highest-volume neighbors so the 2-hop graph stays small.
MAX_NEIGHBORS_TO_EXPAND = 30

MAX_GRAPH_HOPS = 2

# Pass-through heuristic. TODO: tune both.
PASSTHROUGH_RATIO = 0.9
PASSTHROUGH_WINDOW_SECONDS = 3600

# Additive scoring. TODO: tune points and bucket cutoffs.
RULE_DIRECT_SANCTIONED_POINTS = 80
RULE_TWO_HOP_SANCTIONED_POINTS = 40
RULE_PASSTHROUGH_POINTS = 25
MAX_SCORE = 100
BUCKET_HIGH_MIN = 70
BUCKET_MEDIUM_MIN = 30

LLM_MODEL = "openai:gpt-4.1-mini"

OFAC_JSON_PATH = DATA_DIR / "ofac_sanctions.json"
