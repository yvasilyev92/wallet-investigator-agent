"""OFAC sanctions lookup and Etherscan scam/phish labels."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from wallet_investigator.config import OFAC_JSON_PATH
from wallet_investigator.etherscan import EtherscanClient, EtherscanError

logger = logging.getLogger(__name__)

_SCAM_TOKENS = ("phish", "hack", "scam", "exploit", "ofac", "sanction")
_ETHERSCAN_LABEL_CACHE: dict[str, list[str]] = {}
_ETHERSCAN_LABELS_DISABLED = False


def load_ofac_records(path: Path | None = None) -> dict[str, dict]:
    """Load sanctioned ETH wallets from local JSON for O(1) lookup.

    Expected shape (from scripts/extract_ofac_eth.py)::

        {"wallets": {"0x...": {"name": "...", "programs": [...], ...}}}

    Legacy ``{"addresses": ["0x..."]}`` files are still accepted.

    Refresh the JSON from the official OFAC SDN_ENHANCED.XML download:
    https://sanctionslist.ofac.treas.gov/Home/SdnList
    """
    json_path = path or OFAC_JSON_PATH
    if not json_path.exists():
        logger.warning("OFAC list not found at %s; using an empty map", json_path)
        return {}
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    wallets = payload.get("wallets")
    if isinstance(wallets, dict):
        return {str(addr).strip().lower(): meta for addr, meta in wallets.items() if str(addr).strip()}

    addresses = payload.get("addresses", payload if isinstance(payload, list) else [])
    return {str(item).strip().lower(): {} for item in addresses if str(item).strip()}


def load_ofac_addresses(path: Path | None = None) -> set[str]:
    return set(load_ofac_records(path))


def _ofac_tags(address: str, records: dict[str, dict]) -> list[str]:
    meta = records.get(address.lower())
    if meta is None:
        return []
    tags = ["OFAC sanctioned"]
    name = str(meta.get("name") or "").strip()
    if name:
        tags.append(name)
    aliases = [str(item) for item in (meta.get("aliases") or []) if str(item).strip()]
    if aliases:
        tags.append("A.K.A. " + ", ".join(aliases[:3]))
    programs = [str(item) for item in (meta.get("programs") or []) if str(item).strip()]
    if programs:
        tags.append("Programs: " + ", ".join(programs))
    authorities = [str(item) for item in (meta.get("legal_authorities") or []) if str(item).strip()]
    if authorities:
        tags.append(authorities[0])
    if meta.get("list_date"):
        tags.append("Listed " + str(meta["list_date"]))
    types = [str(item) for item in (meta.get("sanctions_types") or []) if str(item).strip()]
    if types:
        tags.append("Type: " + ", ".join(types))
    currencies = [str(item) for item in (meta.get("currency_types") or []) if str(item).strip()]
    if currencies:
        tags.append("Asset: " + ", ".join(currencies))
    if meta.get("reliability"):
        tags.append("Reliability: " + str(meta["reliability"]))
    secondary = [str(item) for item in (meta.get("secondary_sanctions") or []) if str(item).strip()]
    if secondary:
        tags.append(secondary[0])
    countries = [str(item) for item in (meta.get("countries") or []) if str(item).strip()]
    if countries:
        tags.append("Nationality: " + ", ".join(countries))
    rels = meta.get("relationships") or []
    if rels and isinstance(rels[0], dict):
        related = rels[0].get("related_name") or ""
        rel_type = rels[0].get("type") or "Related to"
        if related:
            tags.append(f"{rel_type}: {related}")
    return tags


def describe_ofac(address: str, records: dict[str, dict] | None) -> str:
    """Short phrase for scoring reasons: address plus SDN name/programs."""
    rec = (records or {}).get(address.lower()) or {}
    name = str(rec.get("name") or "").strip()
    label = f"{address} ({name})" if name else address
    extras: list[str] = []
    aliases = [str(item) for item in (rec.get("aliases") or []) if str(item).strip()]
    if aliases:
        extras.append("A.K.A. " + ", ".join(aliases[:2]))
    programs = [str(item) for item in (rec.get("programs") or []) if str(item).strip()]
    if programs:
        extras.append("programs: " + ", ".join(programs))
    if rec.get("list_date"):
        extras.append("listed " + str(rec["list_date"]))
    if extras:
        return f"{label} [{'; '.join(extras)}]"
    return label


class LabelChecker:
    """Check one address at a time; cache hits in memory for the process lifetime."""

    def __init__(self, ofac: set[str] | dict[str, dict] | None = None) -> None:
        if isinstance(ofac, dict):
            self.records = {key.lower(): value for key, value in ofac.items()}
            self.ofac = set(self.records)
        elif ofac is not None:
            self.records = {addr.lower(): {} for addr in ofac}
            self.ofac = set(self.records)
        else:
            self.records = load_ofac_records()
            self.ofac = set(self.records)

    def is_sanctioned(self, address: str) -> bool:
        return address.lower() in self.ofac

    async def etherscan_labels(self, address: str, client: EtherscanClient) -> list[str]:
        global _ETHERSCAN_LABELS_DISABLED
        if _ETHERSCAN_LABELS_DISABLED:
            return []
        key = address.lower()
        if key in _ETHERSCAN_LABEL_CACHE:
            return _ETHERSCAN_LABEL_CACHE[key]
        try:
            labels = await client.get_address_labels(key)
        except EtherscanError as exc:
            logger.info("Etherscan nametag API unavailable; skipping further label calls: %s", exc)
            _ETHERSCAN_LABELS_DISABLED = True
            labels = []
        except Exception as exc:
            logger.info("Etherscan labels unavailable for %s: %s", key, exc)
            labels = []
        _ETHERSCAN_LABEL_CACHE[key] = labels
        return labels

    def scam_labels(self, labels: list[str]) -> list[str]:
        hits = []
        for label in labels:
            lowered = label.lower()
            if any(token in lowered for token in _SCAM_TOKENS):
                hits.append(label)
        return hits

    async def labels_for_wallets(
        self,
        addresses: list[str],
        client: EtherscanClient,
    ) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for address in addresses:
            tags = _ofac_tags(address, self.records)
            etherscan = await self.etherscan_labels(address, client)
            for label in self.scam_labels(etherscan) + etherscan:
                if label not in tags:
                    tags.append(label)
            out[address] = tags
        return out
