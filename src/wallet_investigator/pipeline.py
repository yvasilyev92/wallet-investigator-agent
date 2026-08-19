"""Investigation pipeline: fetch → graph → labels → scores."""

from __future__ import annotations

from wallet_investigator.address import is_valid_eth_address, normalize_address
from wallet_investigator.etherscan import EtherscanClient
from wallet_investigator.graph_builder import build_graph, graph_to_models
from wallet_investigator.labels import LabelChecker
from wallet_investigator.models import InvestigationResult
from wallet_investigator.scoring import score_graph


async def investigate(address: str) -> InvestigationResult:
    if not is_valid_eth_address(address):
        raise ValueError(
            "Invalid Ethereum address. Use a 0x-prefixed, 42-character hex string."
        )
    target = normalize_address(address)

    async with EtherscanClient() as client:
        graph, txs_by_wallet = await build_graph(target, client)
        checker = LabelChecker()
        labels = await checker.labels_for_wallets(list(graph.nodes), client)
        scores = score_graph(graph, txs_by_wallet, checker.ofac, checker.records)

    nodes, edges = graph_to_models(graph)
    return InvestigationResult(
        target=target,
        nodes=nodes,
        edges=edges,
        scores=scores,
        labels=labels,
    )
