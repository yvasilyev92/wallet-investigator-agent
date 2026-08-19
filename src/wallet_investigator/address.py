"""Ethereum address validation."""

from __future__ import annotations

import re

_ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def is_valid_eth_address(value: str | None) -> bool:
    """Return True if value is a 0x-prefixed, 42-character hex address."""
    if not value:
        return False
    return bool(_ETH_ADDRESS_RE.fullmatch(value.strip()))


def normalize_address(value: str) -> str:
    """Lowercase a validated address for consistent graph keys."""
    return value.strip().lower()
