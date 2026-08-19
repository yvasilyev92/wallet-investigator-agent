"""Shared Pydantic models for the investigation pipeline."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_serializer


class Transaction(BaseModel):
    hash: str
    from_address: str
    to_address: str
    value_wei: int
    timestamp: int
    is_error: bool = False


class GraphNode(BaseModel):
    address: str
    hop: int
    tx_volume_wei: int = 0
    tx_count: int = 0

    @field_serializer("tx_volume_wei", when_used="json")
    def _serialize_tx_volume_wei(self, value: int) -> str:
        # Dash/orjson cannot encode ints larger than 2^63-1 (a few ETH in wei).
        return str(value)


class GraphEdge(BaseModel):
    source: str
    target: str
    tx_count: int
    value_wei: int

    @field_serializer("value_wei", when_used="json")
    def _serialize_value_wei(self, value: int) -> str:
        return str(value)


class RuleHit(BaseModel):
    rule_id: str
    points: int
    reason: str


class WalletScore(BaseModel):
    address: str
    score: int
    bucket: Literal["low", "medium", "high"]
    reasons: list[RuleHit] = Field(default_factory=list)


class InvestigationResult(BaseModel):
    target: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    scores: dict[str, WalletScore]
    labels: dict[str, list[str]] = Field(default_factory=dict)
