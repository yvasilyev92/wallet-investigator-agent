"""Build a 2-hop wallet graph from Ethereum transactions."""

from __future__ import annotations

from collections import defaultdict

import networkx as nx

from wallet_investigator.config import (
    DUST_THRESHOLD_WEI,
    HIGH_TX_COUNT_THRESHOLD,
    MAX_GRAPH_HOPS,
    MAX_NEIGHBORS_TO_EXPAND,
)
from wallet_investigator.etherscan import EtherscanClient
from wallet_investigator.models import GraphEdge, GraphNode, Transaction


def _counterparty(wallet: str, tx: Transaction) -> str | None:
    # TODO: DUST_THRESHOLD_WEI — skip tiny transfers that are usually spam.
    if tx.is_error or not tx.to_address or tx.value_wei < DUST_THRESHOLD_WEI:
        return None
    if tx.from_address == wallet and tx.to_address != wallet:
        return tx.to_address
    if tx.to_address == wallet and tx.from_address != wallet:
        return tx.from_address
    return None


def _add_edge(graph: nx.DiGraph, source: str, target: str, value_wei: int) -> None:
    if graph.has_edge(source, target):
        graph[source][target]["tx_count"] += 1
        graph[source][target]["value_wei"] += value_wei
    else:
        graph.add_edge(source, target, tx_count=1, value_wei=value_wei)


def _top_counterparties(wallet: str, txs: list[Transaction]) -> list[str]:
    volume: dict[str, int] = defaultdict(int)
    for tx in txs:
        other = _counterparty(wallet, tx)
        if other is None:
            continue
        volume[other] += tx.value_wei
    ranked = sorted(volume, key=volume.get, reverse=True)
    return ranked[:MAX_NEIGHBORS_TO_EXPAND]


async def build_graph(
    target: str,
    client: EtherscanClient,
) -> tuple[nx.DiGraph, dict[str, list[Transaction]]]:
    """BFS out to MAX_GRAPH_HOPS, pruning dust, hubs, and excess neighbors."""
    graph = nx.DiGraph()
    graph.add_node(target, hop=0)
    txs_by_wallet: dict[str, list[Transaction]] = {}

    frontier = [target]
    expanded: set[str] = set()

    for hop in range(MAX_GRAPH_HOPS):
        next_frontier: list[str] = []
        for wallet in frontier:
            if wallet in expanded:
                continue
            expanded.add(wallet)

            txs = await client.get_transactions(wallet)
            txs_by_wallet[wallet] = txs

            # TODO: MAX_NEIGHBORS_TO_EXPAND — only keep highest-volume counterparties
            # so a busy wallet does not dump hundreds of nodes into the graph.
            top = set(_top_counterparties(wallet, txs))
            for tx in txs:
                other = _counterparty(wallet, tx)
                if other is None or other not in top:
                    continue
                source, dest = tx.from_address, tx.to_address
                if source not in graph:
                    graph.add_node(source, hop=hop + 1)
                if dest not in graph:
                    graph.add_node(dest, hop=hop + 1)
                _add_edge(graph, source, dest, tx.value_wei)

            # TODO: HIGH_TX_COUNT_THRESHOLD is a coarse exchange/service proxy.
            # Keep this wallet's top edges, but do not BFS through it.
            if len(txs) >= HIGH_TX_COUNT_THRESHOLD:
                continue

            for neighbor in top:
                node_hop = graph.nodes[neighbor].get("hop", hop + 1)
                graph.nodes[neighbor]["hop"] = min(node_hop, hop + 1)
                if neighbor not in expanded and neighbor not in next_frontier:
                    next_frontier.append(neighbor)

        frontier = next_frontier

    _annotate_volumes(graph, txs_by_wallet)
    return graph, txs_by_wallet


def _annotate_volumes(
    graph: nx.DiGraph,
    txs_by_wallet: dict[str, list[Transaction]],
) -> None:
    for address in graph.nodes:
        volume = 0
        count = 0
        for tx in txs_by_wallet.get(address, []):
            if tx.is_error:
                continue
            volume += tx.value_wei
            count += 1
        if count == 0:
            volume = 0
            for _, _, data in graph.in_edges(address, data=True):
                volume += data.get("value_wei", 0)
                count += data.get("tx_count", 0)
            for _, _, data in graph.out_edges(address, data=True):
                volume += data.get("value_wei", 0)
                count += data.get("tx_count", 0)
        graph.nodes[address]["tx_volume_wei"] = volume
        graph.nodes[address]["tx_count"] = count


def graph_to_models(graph: nx.DiGraph) -> tuple[list[GraphNode], list[GraphEdge]]:
    nodes = [
        GraphNode(
            address=address,
            hop=int(data.get("hop", 0)),
            tx_volume_wei=int(data.get("tx_volume_wei", 0)),
            tx_count=int(data.get("tx_count", 0)),
        )
        for address, data in graph.nodes(data=True)
    ]
    edges = [
        GraphEdge(
            source=source,
            target=target,
            tx_count=int(data.get("tx_count", 1)),
            value_wei=int(data.get("value_wei", 0)),
        )
        for source, target, data in graph.edges(data=True)
    ]
    return nodes, edges
