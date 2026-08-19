"""Click-only case-file agent: tools over the investigation result + local OFAC data."""

from __future__ import annotations

import json
import logging

from wallet_investigator.config import LLM_MODEL, OPENAI_API_KEY
from wallet_investigator.labels import load_ofac_records
from wallet_investigator.models import GraphEdge, InvestigationResult, WalletScore
from wallet_investigator.programs import lookup_ofac_program

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a senior blockchain-forensics analyst writing a case file for a compliance officer.

You are investigating ONE Ethereum mainnet wallet the user clicked. Tools only reveal facts already computed by the application (heuristic score, OFAC SDN extract, graph neighbors, labels). You have no raw mempool dump.

Hard rules:
- NEVER change, second-guess, or re-score the heuristic risk number. Quote it as given.
- NEVER invent OFAC listings, program meanings, transaction hashes, dollar amounts, or counterparties that tools did not return.
- NEVER browse the web. If lookup_program has no entry, say so and rely on legal_authorities from get_ofac_record.
- If a wallet is not on OFAC, say that clearly. Absence of a listing is not a clean bill of health.
- Caveat the graph: it is capped, dust-pruned, and at most 2 hops.

Write a polished markdown case file with these sections (use ## headings):

## Verdict
The frozen score/bucket and what it means in one tight paragraph.

## Identity
Who OFAC says this is (name, aliases, entity type, nationality), or that it is not on the local SDN extract. Include list date, legal authority, programs (expand codes via lookup_program), secondary-sanctions language, and related entities when present.

## Why this score
Walk through each heuristic rule that fired (and note any that did not). Tie OFAC names to the rule text.

## Network context
What the current investigation graph shows: hop from the analyst's target, inbound/outbound counterparties, which neighbors are themselves OFAC-listed, notable labels. Do not claim flows you cannot see.

## Screening labels
Etherscan / OFAC pills already attached to this wallet.

## Caveats
Graph limits, heuristic nature, not legal advice.

Be specific and dense with the facts the tools returned (names, dates, EOs, neighbor counts). Aim for a briefing a partner could paste into an email — several short paragraphs per section, not two sentences total.
"""

_USER_PROMPT = """Write the full case file for clicked wallet {address}.
The analyst's original investigation target is {target}.
Call tools as needed, then produce the markdown report."""


def fallback_report(address: str, result: InvestigationResult) -> str:
    """Structured markdown when the LLM/agent cannot run."""
    ofac = load_ofac_records()
    score = result.scores.get(address)
    if score is None:
        return f"No score is available for `{address}`."

    lines = [
        "## Verdict",
        f"Heuristic score **{score.score}/100** ({score.bucket} risk) for `{address}`. "
        "This number was computed by additive rules and was not produced by a model.",
        "",
        "## Identity",
        _ofac_markdown(address, ofac),
        "",
        "## Why this score",
    ]
    if score.reasons:
        for hit in score.reasons:
            lines.append(f"- **{hit.rule_id}** ({hit.points} pts): {hit.reason}")
    else:
        lines.append("No heuristic rules fired for this wallet.")
    lines.extend(["", "## Network context", _neighbors_markdown(address, result, ofac)])
    labels = result.labels.get(address) or []
    lines.extend(
        [
            "",
            "## Screening labels",
            ", ".join(f"`{item}`" for item in labels) if labels else "No labels attached.",
            "",
            "## Caveats",
            "Graph is Ethereum mainnet only, at most two hops, with dust/hub pruning and a per-wallet tx cap. "
            "This is not a legal determination.",
        ]
    )
    return "\n".join(lines)


def _ofac_markdown(address: str, ofac: dict[str, dict]) -> str:
    rec = ofac.get(address.lower())
    if not rec:
        return "Not present on the local OFAC SDN extract used by this app."
    bits = [f"**{rec.get('name') or 'OFAC-listed party'}**"]
    if rec.get("entity_type"):
        bits.append(str(rec["entity_type"]))
    if rec.get("aliases"):
        bits.append("A.K.A. " + ", ".join(rec["aliases"][:5]))
    if rec.get("programs"):
        bits.append("Programs: " + ", ".join(rec["programs"]))
    if rec.get("legal_authorities"):
        bits.append("; ".join(rec["legal_authorities"]))
    if rec.get("list_date"):
        bits.append("Listed " + str(rec["list_date"]))
    if rec.get("secondary_sanctions"):
        bits.append(str(rec["secondary_sanctions"][0]))
    if rec.get("countries"):
        bits.append("Nationality: " + ", ".join(rec["countries"]))
    return ". ".join(bits) + "."


def _neighbor_rows(address: str, result: InvestigationResult) -> tuple[list[GraphEdge], list[GraphEdge]]:
    inbound = [edge for edge in result.edges if edge.target == address]
    outbound = [edge for edge in result.edges if edge.source == address]
    return inbound, outbound


def _neighbors_markdown(address: str, result: InvestigationResult, ofac: dict[str, dict]) -> str:
    node = next((item for item in result.nodes if item.address == address), None)
    inbound, outbound = _neighbor_rows(address, result)
    hop = node.hop if node else "?"
    role = "investigation target" if address == result.target else f"hop {hop} from target `{result.target}`"
    lines = [
        f"This wallet is the **{role}**. "
        f"Graph degree: {len(inbound)} inbound / {len(outbound)} outbound counterparties "
        f"(after pruning).",
    ]
    ofac_neighbors: list[str] = []
    for edge in inbound + outbound:
        other = edge.source if edge.target == address else edge.target
        rec = ofac.get(other)
        if rec:
            ofac_neighbors.append(f"`{other}` ({rec.get('name') or 'listed'})")
    if ofac_neighbors:
        # unique preserve order
        seen: list[str] = []
        for item in ofac_neighbors:
            if item not in seen:
                seen.append(item)
        lines.append("OFAC-listed neighbors in this graph: " + "; ".join(seen[:8]) + ".")
    else:
        lines.append("No OFAC-listed counterparties appear among the currently expanded neighbors.")
    return " ".join(lines)


def _tool_json(data: object) -> str:
    return json.dumps(data, default=str, indent=2)


def _build_tools(address: str, result: InvestigationResult, ofac: dict[str, dict]):
    from langchain.tools import tool

    key = address.lower()

    @tool
    def get_score() -> str:
        """Return the frozen heuristic score, bucket, and rule reasons for the clicked wallet. Do not change these numbers."""
        score = result.scores.get(key) or result.scores.get(address)
        if score is None:
            return _tool_json({"error": "no score"})
        return _tool_json(
            {
                "address": score.address,
                "score": score.score,
                "bucket": score.bucket,
                "reasons": [hit.model_dump() for hit in score.reasons],
                "is_investigation_target": score.address == result.target,
            }
        )

    @tool
    def get_ofac_record() -> str:
        """Return local OFAC SDN metadata for the clicked wallet, if it is on the extract."""
        rec = ofac.get(key)
        if not rec:
            return _tool_json({"listed": False, "address": key})
        return _tool_json({"listed": True, "address": key, **rec})

    @tool
    def get_neighbor_context() -> str:
        """Return 1-hop counterparties already in the investigation graph, with scores and OFAC names."""
        node = next((item for item in result.nodes if item.address == key), None)
        inbound, outbound = _neighbor_rows(key, result)

        def pack(edge: GraphEdge, other: str) -> dict:
            other_score = result.scores.get(other)
            rec = ofac.get(other) or {}
            return {
                "address": other,
                "edge_tx_count": edge.tx_count,
                "score": other_score.score if other_score else None,
                "bucket": other_score.bucket if other_score else None,
                "ofac_name": rec.get("name") or None,
                "ofac_programs": rec.get("programs") or [],
            }

        ins = [pack(edge, edge.source) for edge in inbound[:15]]
        outs = [pack(edge, edge.target) for edge in outbound[:15]]
        return _tool_json(
            {
                "clicked": key,
                "target": result.target,
                "hop": node.hop if node else None,
                "tx_count_observed": node.tx_count if node else None,
                "inbound_count": len(inbound),
                "outbound_count": len(outbound),
                "inbound": ins,
                "outbound": outs,
            }
        )

    @tool
    def lookup_program(code: str) -> str:
        """Plain-English description of an OFAC program code such as DPRK3, SDGT, or ILLICIT-DRUGS-EO14059."""
        return lookup_ofac_program(code)

    @tool
    def get_labels() -> str:
        """Return screening labels already computed for the clicked wallet (OFAC pills and Etherscan nametags)."""
        return _tool_json({"address": key, "labels": result.labels.get(key) or result.labels.get(address) or []})

    return [get_score, get_ofac_record, get_neighbor_context, lookup_program, get_labels]


def _message_text(message: object) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            else:
                text = getattr(item, "text", None)
                if text:
                    parts.append(str(text))
        return "\n".join(part for part in parts if part).strip()
    return str(content).strip()


def run_case_agent(address: str, result: InvestigationResult) -> str:
    """Tool-using case file for one clicked wallet. Falls back to local markdown."""
    address = address.lower()
    if not OPENAI_API_KEY:
        return fallback_report(address, result)

    ofac = load_ofac_records()
    try:
        from langchain.agents import create_agent
        from langchain.chat_models import init_chat_model

        model = init_chat_model(LLM_MODEL, api_key=OPENAI_API_KEY, temperature=0)
        agent = create_agent(
            model,
            tools=_build_tools(address, result, ofac),
            system_prompt=_SYSTEM_PROMPT,
        )
        payload = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": _USER_PROMPT.format(address=address, target=result.target),
                    }
                ]
            },
            {"recursion_limit": 16},
        )
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not messages:
            return fallback_report(address, result)
        text = _message_text(messages[-1])
        return text or fallback_report(address, result)
    except Exception as exc:
        logger.warning("Case-file agent failed; using fallback: %s", exc)
        return fallback_report(address, result)
