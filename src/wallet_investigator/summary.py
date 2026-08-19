"""LLM / agent write-ups for a clicked wallet."""

from wallet_investigator.agent import fallback_report, run_case_agent
from wallet_investigator.models import InvestigationResult, WalletScore


def fallback_summary(score: WalletScore) -> str:
    if not score.reasons:
        return (
            f"Score {score.score}/100 ({score.bucket} risk). "
            "No heuristic rules fired for this wallet."
        )
    joined = " ".join(hit.reason for hit in score.reasons)
    return f"Score {score.score}/100 ({score.bucket} risk). {joined}"


def summarize_wallet(address: str, result: InvestigationResult) -> str:
    """Run the click-only case-file agent for one wallet."""
    return run_case_agent(address, result)
