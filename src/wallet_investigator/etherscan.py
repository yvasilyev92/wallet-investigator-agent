"""Etherscan V2 client for Ethereum mainnet transaction history."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from wallet_investigator.config import (
    ETHEREUM_CHAIN_ID,
    ETHERSCAN_API_KEY,
    ETHERSCAN_MAX_RETRIES,
    ETHERSCAN_MIN_INTERVAL_SECONDS,
    ETHERSCAN_V2_URL,
    MAX_TX_PER_WALLET,
    TXLIST_PAGE_SIZE,
)
from wallet_investigator.models import Transaction

logger = logging.getLogger(__name__)


class EtherscanError(RuntimeError):
    pass


class RateLimiter:
    def __init__(self, min_interval: float = ETHERSCAN_MIN_INTERVAL_SECONDS) -> None:
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            gap = self.min_interval - (now - self._last)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last = time.monotonic()


def _is_rate_limited(data: dict) -> bool:
    result = data.get("result", "")
    message = str(data.get("message", ""))
    blob = f"{result} {message}".lower()
    return "rate limit" in blob or "max calls" in blob


class EtherscanClient:
    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else ETHERSCAN_API_KEY
        if not self.api_key:
            raise EtherscanError("ETHERSCAN_API_KEY is not set")
        self._client = client
        self._owns_client = client is None
        self._limiter = RateLimiter()

    async def __aenter__(self) -> EtherscanClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, params: dict[str, str | int]) -> dict:
        if self._client is None:
            raise EtherscanError("EtherscanClient must be used as an async context manager")

        query = {
            "chainid": ETHEREUM_CHAIN_ID,
            "apikey": self.api_key,
            **params,
        }
        delay = 0.5
        last_error = "Etherscan request failed"
        for attempt in range(ETHERSCAN_MAX_RETRIES):
            await self._limiter.wait()
            response = await self._client.get(ETHERSCAN_V2_URL, params=query)
            response.raise_for_status()
            data = response.json()
            if _is_rate_limited(data):
                last_error = str(data.get("result") or data.get("message") or "rate limited")
                logger.warning("Etherscan rate limit (attempt %s): %s", attempt + 1, last_error)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            return data
        raise EtherscanError(last_error)

    async def get_transactions(self, address: str) -> list[Transaction]:
        """Fetch normal transactions for an address, paginating up to MAX_TX_PER_WALLET."""
        collected: list[Transaction] = []
        page = 1
        while len(collected) < MAX_TX_PER_WALLET:
            remaining = MAX_TX_PER_WALLET - len(collected)
            offset = min(TXLIST_PAGE_SIZE, remaining)
            data = await self._get(
                {
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "startblock": 0,
                    "endblock": 99999999,
                    "page": page,
                    "offset": offset,
                    "sort": "asc",
                }
            )
            status = str(data.get("status", "0"))
            message = str(data.get("message", ""))
            result = data.get("result", [])

            if status == "0":
                blob = f"{message} {result}".lower()
                if "no transactions found" in blob:
                    break
                if isinstance(result, str) and not result.strip():
                    break
                raise EtherscanError(str(result) if result else message or "txlist failed")

            if not isinstance(result, list) or not result:
                break

            for raw in result:
                to_address = (raw.get("to") or "").strip().lower()
                from_address = (raw.get("from") or "").strip().lower()
                if not from_address:
                    continue
                collected.append(
                    Transaction(
                        hash=raw.get("hash", ""),
                        from_address=from_address,
                        to_address=to_address,
                        value_wei=int(raw.get("value") or 0),
                        timestamp=int(raw.get("timeStamp") or 0),
                        is_error=str(raw.get("isError", "0")) != "0",
                    )
                )
                if len(collected) >= MAX_TX_PER_WALLET:
                    break

            if len(result) < offset:
                break
            page += 1

        return collected

    async def get_address_labels(self, address: str) -> list[str]:
        """Fetch Etherscan nametag labels for one address.

        TODO: There is no clean free-tier bulk label API. getaddresstag is a
        Pro Plus endpoint and must be called per address (or small batches).
        Cache results in memory and treat failures as "no labels".
        """
        data = await self._get(
            {
                "module": "nametag",
                "action": "getaddresstag",
                "address": address,
            }
        )
        if str(data.get("status")) != "1":
            result = data.get("result", "")
            message = str(data.get("message", ""))
            blob = f"{result} {message}".lower()
            if "api pro" in blob or "upgrade" in blob:
                raise EtherscanError("Etherscan nametag API is not available on this key")
            return []
        result = data.get("result") or []
        if not isinstance(result, list) or not result:
            return []
        entry = result[0] if isinstance(result[0], dict) else {}
        labels = entry.get("labels") or []
        slugs = entry.get("labels_slug") or []
        nametag = entry.get("nametag") or entry.get("name") or ""
        combined: list[str] = []
        for item in list(labels) + list(slugs):
            text = str(item).strip()
            if text and text not in combined:
                combined.append(text)
        if nametag and str(nametag) not in combined:
            combined.insert(0, str(nametag))
        return combined
