"""Dash UI for Ethereum wallet investigations."""

from __future__ import annotations

import asyncio
import math
from pathlib import Path

import dash_cytoscape as cyto
from dash import Dash, Input, Output, State, callback, dcc, html, no_update

from wallet_investigator.address import is_valid_eth_address, normalize_address
from wallet_investigator.models import InvestigationResult, WalletScore
from wallet_investigator.pipeline import investigate
from wallet_investigator.summary import summarize_wallet

ASSETS = Path(__file__).parent / "assets"

app = Dash(
    __name__,
    title="Wallet Investigator",
    assets_folder=str(ASSETS),
    suppress_callback_exceptions=True,
    external_stylesheets=[
        "https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap"
    ],
)

BUCKET_COLOR = {"low": "#22c55e", "medium": "#f59e0b", "high": "#ef4444"}

CYTO_STYLESHEET = [
    {
        "selector": "node",
        "style": {
            "label": "data(label)",
            "color": "#e2e8f0",
            "font-size": "10px",
            "font-family": "DM Sans, sans-serif",
            "text-valign": "bottom",
            "text-halign": "center",
            "text-margin-y": 6,
            "background-color": "data(color)",
            "width": "data(size)",
            "height": "data(size)",
            "border-width": 2,
            "border-color": "rgba(255,255,255,0.35)",
        },
    },
    {
        "selector": "node.target",
        "style": {
            "border-width": 4,
            "border-color": "#7dd3fc",
            "font-weight": "600",
        },
    },
    {
        "selector": "edge",
        "style": {
            "width": "data(width)",
            "line-color": "rgba(148,163,184,0.45)",
            "target-arrow-color": "rgba(148,163,184,0.45)",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "arrow-scale": 0.8,
        },
    },
]


def _short(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}"


def _node_size(volume: int, max_volume: int) -> int:
    if max_volume <= 0:
        return 32
    t = math.log1p(volume) / math.log1p(max_volume)
    return int(26 + t * 42)


def result_to_elements(result: InvestigationResult) -> list[dict]:
    max_volume = max((node.tx_volume_wei for node in result.nodes), default=0)
    elements: list[dict] = []
    for node in result.nodes:
        score = result.scores[node.address]
        elements.append(
            {
                "data": {
                    "id": node.address,
                    "label": _short(node.address),
                    "color": BUCKET_COLOR[score.bucket],
                    "size": _node_size(node.tx_volume_wei, max_volume),
                    "bucket": score.bucket,
                },
                "classes": "target" if node.address == result.target else "",
            }
        )
    max_count = max((edge.tx_count for edge in result.edges), default=1)
    for edge in result.edges:
        elements.append(
            {
                "data": {
                    "source": edge.source,
                    "target": edge.target,
                    "width": 1 + 4 * (edge.tx_count / max_count),
                }
            }
        )
    return elements


def _empty_panel() -> html.Div:
    return html.Div(
        [
            html.H3("Wallet detail"),
            html.P(
                "Click a node on the graph to open a case file. The agent only runs for the wallet you select.",
                className="muted",
            ),
        ]
    )


def _panel(score: WalletScore, labels: list[str], summary: str) -> html.Div:
    reasons = [
        html.Li(f"{hit.points} pts — {hit.reason}") for hit in score.reasons
    ] or [html.Li("No heuristic rules fired.")]
    label_pills = [
        html.Span(label, className="pill") for label in labels
    ] or [html.Span("No labels", className="pill quiet")]
    return html.Div(
        [
            html.H3("Wallet detail"),
            html.P(score.address, className="mono address-full"),
            html.Div(
                className="score-row",
                children=[
                    html.Span(str(score.score), className=f"score-num {score.bucket}"),
                    html.Span("/100", className="score-den"),
                    html.Span(score.bucket.upper(), className=f"bucket-tag {score.bucket}"),
                ],
            ),
            html.H4("Labels"),
            html.Div(label_pills, className="pills"),
            html.H4("Score breakdown"),
            html.Ul(reasons, className="reasons"),
            html.H4("Case file"),
            dcc.Markdown(summary, className="case-file", link_target="_blank"),
        ]
    )


LOADING_SPINNER = html.Div(
    [
        html.Div(className="spinner"),
        html.Div("Investigation in progress", className="loading-copy"),
    ],
    className="loading-box",
)

app.layout = html.Div(
    className="page",
    children=[
        html.Div(
            className="card",
            children=[
                html.P("Ethereum mainnet", className="eyebrow"),
                html.H1("Wallet Investigator"),
                html.P(
                    "Enter an Ethereum address to map connected wallets, screen OFAC and Etherscan labels, and score simple risk heuristics.",
                    className="lede",
                ),
                html.Div(
                    className="search-row",
                    children=[
                        dcc.Input(
                            id="address-input",
                            type="text",
                            placeholder="0x…",
                            className="address-input",
                            debounce=False,
                            maxLength=42,
                            autoComplete="off",
                            spellCheck=False,
                        ),
                        html.Button(
                            "Investigate",
                            id="investigate-btn",
                            className="investigate-btn",
                            n_clicks=0,
                        ),
                    ],
                ),
                html.Div(id="validation-error", className="error-msg"),
            ],
        ),
        html.Div(
            className="legend",
            children=[
                html.Span("Risk", className="legend-label"),
                html.Span(className="dot low"),
                html.Span("Low"),
                html.Span(className="dot medium"),
                html.Span("Medium"),
                html.Span(className="dot high"),
                html.Span("High"),
                html.Span("· node size = tx volume", className="muted"),
            ],
        ),
        dcc.Store(id="run-store"),
        dcc.Store(id="result-store"),
        dcc.Store(id="summary-cache", data={}),
        dcc.Loading(
            id="loading",
            custom_spinner=LOADING_SPINNER,
            show_initially=False,
            children=html.Div(id="results-slot"),
        ),
    ],
)


@callback(
    Output("validation-error", "children"),
    Output("run-store", "data"),
    Input("investigate-btn", "n_clicks"),
    State("address-input", "value"),
    prevent_initial_call=True,
)
def validate_address(n_clicks: int, value: str | None):
    if not is_valid_eth_address(value):
        return (
            "Invalid address. Use a 0x-prefixed, 42-character hex string (0x + 40 hex characters).",
            no_update,
        )
    return "", {"address": normalize_address(value), "n": n_clicks}


@callback(
    Output("results-slot", "children"),
    Output("result-store", "data"),
    Output("summary-cache", "data"),
    Input("run-store", "data"),
    running=[(Output("investigate-btn", "disabled"), True, False)],
    prevent_initial_call=True,
)
def run_investigation(payload: dict):
    address = payload["address"]
    try:
        result = asyncio.run(investigate(address))
    except Exception as exc:
        error = html.Div(str(exc), className="error-msg visible banner")
        return error, None, {}

    elements = result_to_elements(result)
    layout = {
        "name": "breadthfirst",
        "roots": f'[id = "{result.target}"]',
        "directed": False,
        "padding": 24,
        "spacingFactor": 1.35,
    }
    workspace = html.Div(
        className="workspace",
        children=[
            html.Div(
                className="graph-card",
                children=[
                    cyto.Cytoscape(
                        id="cyto",
                        elements=elements,
                        stylesheet=CYTO_STYLESHEET,
                        layout=layout,
                        style={"width": "100%", "height": "100%"},
                        minZoom=0.3,
                        maxZoom=2.5,
                    )
                ],
            ),
            html.Div(
                className="panel-card",
                children=dcc.Loading(
                    id="case-loading",
                    show_initially=False,
                    custom_spinner=html.Div(
                        [
                            html.Div(className="spinner"),
                            html.Div("Writing case file", className="loading-copy"),
                        ],
                        className="loading-box",
                    ),
                    children=html.Div(id="side-panel", children=[_empty_panel()]),
                ),
            ),
        ],
    )
    return workspace, result.model_dump(mode="json"), {}


@callback(
    Output("side-panel", "children"),
    Output("summary-cache", "data", allow_duplicate=True),
    Input("cyto", "tapNodeData"),
    State("result-store", "data"),
    State("summary-cache", "data"),
    prevent_initial_call=True,
)
def show_wallet(node: dict | None, stored: dict | None, cache: dict | None):
    if not node or not stored:
        return no_update, no_update
    result = InvestigationResult.model_validate(stored)
    address = node["id"]
    score = result.scores[address]
    labels = result.labels.get(address, [])
    cache = dict(cache or {})
    if address not in cache:
        cache[address] = summarize_wallet(address, result)
    return _panel(score, labels, cache[address]), cache


def main() -> None:
    app.run(debug=True, host="127.0.0.1", port=8050)


if __name__ == "__main__":
    main()
