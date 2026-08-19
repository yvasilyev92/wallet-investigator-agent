"""Simple additive heuristic scoring rules."""

from __future__ import annotations

import networkx as nx

from wallet_investigator.config import (
    BUCKET_HIGH_MIN,
    BUCKET_MEDIUM_MIN,
    MAX_SCORE,
    PASSTHROUGH_RATIO,
    PASSTHROUGH_WINDOW_SECONDS,
    RULE_DIRECT_SANCTIONED_POINTS,
    RULE_PASSTHROUGH_POINTS,
    RULE_TWO_HOP_SANCTIONED_POINTS,
)
from wallet_investigator.labels import describe_ofac
from wallet_investigator.models import RuleHit, Transaction, WalletScore


def _bucket(score: int) -> str:
    if score >= BUCKET_HIGH_MIN:
        return "high"
    if score >= BUCKET_MEDIUM_MIN:
        return "medium"
    return "low"


def _tx_counterparties(address: str, txs: list[Transaction]) -> set[str]:
    others: set[str] = set()
    for tx in txs:
        if tx.is_error:
            continue
        if tx.from_address == address and tx.to_address:
            others.add(tx.to_address)
        if tx.to_address == address and tx.from_address:
            others.add(tx.from_address)
    return others


def rule_direct_sanctioned(
    address: str,
    graph: nx.DiGraph,
    txs: list[Transaction],
    sanctioned: set[str],
    records: dict[str, dict] | None = None,
) -> RuleHit | None:
    """Hit if this wallet transacted directly with an OFAC-listed address."""
    if address in sanctioned:
        return RuleHit(
            rule_id="direct_sanctioned",
            points=RULE_DIRECT_SANCTIONED_POINTS,
            reason=f"{describe_ofac(address, records)} is itself on the OFAC sanctions list.",
        )
    neighbors = set(graph.successors(address)) | set(graph.predecessors(address))
    hits = sorted((_tx_counterparties(address, txs) | neighbors) & sanctioned)
    if not hits:
        return None
    shown = ", ".join(describe_ofac(item, records) for item in hits[:3])
    extra = f" (and {len(hits) - 3} more)" if len(hits) > 3 else ""
    return RuleHit(
        rule_id="direct_sanctioned",
        points=RULE_DIRECT_SANCTIONED_POINTS,
        reason=f"Direct transaction with sanctioned address {shown}{extra}.",
    )


def rule_two_hop_sanctioned(
    address: str,
    graph: nx.DiGraph,
    txs: list[Transaction],
    sanctioned: set[str],
    records: dict[str, dict] | None = None,
) -> RuleHit | None:
    """Hit if a sanctioned address is exactly two hops away (not a direct neighbor)."""
    if address not in graph:
        return None
    try:
        distances = nx.single_source_shortest_path_length(graph.to_undirected(), address, cutoff=2)
    except nx.NetworkXError:
        return None
    direct = _tx_counterparties(address, txs) | set(graph.successors(address)) | set(
        graph.predecessors(address)
    )
    hop2 = [
        other
        for other, dist in distances.items()
        if dist == 2 and other in sanctioned and other not in direct
    ]
    if not hop2:
        return None
    shown = ", ".join(describe_ofac(item, records) for item in sorted(hop2)[:3])
    extra = f" (and {len(hop2) - 3} more)" if len(hop2) > 3 else ""
    return RuleHit(
        rule_id="two_hop_sanctioned",
        points=RULE_TWO_HOP_SANCTIONED_POINTS,
        reason=f"Sanctioned address within 2 hops: {shown}{extra}.",
    )


def rule_passthrough(address: str, txs: list[Transaction]) -> RuleHit | None:
    """Hit if most incoming value leaves again within a short window.

    TODO: PASSTHROUGH_RATIO and PASSTHROUGH_WINDOW_SECONDS need tuning.
    This is a coarse mixer/peel-chain proxy, not a full flow trace.
    """
    incoming = [
        tx
        for tx in txs
        if tx.to_address == address and not tx.is_error and tx.value_wei > 0
    ]
    outgoing = [
        tx
        for tx in txs
        if tx.from_address == address and not tx.is_error and tx.value_wei > 0
    ]
    if not incoming or not outgoing:
        return None

    in_total = sum(tx.value_wei for tx in incoming)
    out_total = sum(tx.value_wei for tx in outgoing)
    if in_total == 0 or out_total / in_total < PASSTHROUGH_RATIO:
        return None

    matched = 0
    for inbound in incoming:
        window_end = inbound.timestamp + PASSTHROUGH_WINDOW_SECONDS
        if any(inbound.timestamp <= tx.timestamp <= window_end for tx in outgoing):
            matched += inbound.value_wei

    if in_total == 0 or matched / in_total < PASSTHROUGH_RATIO:
        return None

    pct = round(100 * out_total / in_total)
    window_hours = PASSTHROUGH_WINDOW_SECONDS / 3600
    return RuleHit(
        rule_id="passthrough",
        points=RULE_PASSTHROUGH_POINTS,
        reason=(
            f"Pass-through pattern: about {pct}% of incoming value left again "
            f"within {window_hours:g} hour(s)."
        ),
    )


def score_wallet(
    address: str,
    graph: nx.DiGraph,
    txs: list[Transaction],
    sanctioned: set[str],
    records: dict[str, dict] | None = None,
) -> WalletScore:
    hits = [
        hit
        for hit in (
            rule_direct_sanctioned(address, graph, txs, sanctioned, records),
            rule_two_hop_sanctioned(address, graph, txs, sanctioned, records),
            rule_passthrough(address, txs),
        )
        if hit is not None
    ]
    score = min(MAX_SCORE, sum(hit.points for hit in hits))
    return WalletScore(address=address, score=score, bucket=_bucket(score), reasons=hits)


def score_graph(
    graph: nx.DiGraph,
    txs_by_wallet: dict[str, list[Transaction]],
    sanctioned: set[str],
    records: dict[str, dict] | None = None,
) -> dict[str, WalletScore]:
    return {
        address: score_wallet(
            address, graph, txs_by_wallet.get(address, []), sanctioned, records
        )
        for address in graph.nodes
    }
