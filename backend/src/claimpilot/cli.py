"""Run claims end to end from the command line. State lives in Postgres, so a claim paused
for review can be resumed later, even after a restart.

    uv run python -m claimpilot.cli submit ../data/claims/demo-001/form.json PHOTO [PHOTO ...]
    uv run python -m claimpilot.cli review CLAIM_ID approve|deny|override [--reason R] [--amount N]
    uv run python -m claimpilot.cli show CLAIM_ID [--json]

Uses LLM_MODE from .env (default `fake`, which uses backend/fixtures/llm/fake/*.json).
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from claimpilot.checkpoint import open_checkpointer
from claimpilot.config import get_settings
from claimpilot.db import get_engine, get_sessionmaker
from claimpilot.domain import ClaimForm
from claimpilot.graphs.claim import ClaimRun, ClaimService, ReviewDecision
from claimpilot.llm import CallRecord, LLMClient
from claimpilot.tools import ToolClient, ToolRecord


def print_run(run: ClaimRun, timeline: list[str]) -> None:
    head = run.status.value.upper() + (f" ({run.decision.value})" if run.decision else "")
    print(f"\nClaim {run.claim_id}  ->  {head}   (core: {run.core_claim_id})")
    if run.error:
        print(f"Error: {run.error}")
    if run.intake and run.intake.errors:
        print("Rejected:", *run.intake.errors, sep="\n  - ")
    if run.intake and run.intake.extraction:
        ex = run.intake.extraction
        print(f"Intake:   {ex.claim_type.value}, severity {ex.severity.value}, {ex.damage_areas}")
    if run.coverage:
        c = run.coverage
        cites = [x.clause_id for x in c.citations]
        print(f"Coverage: {c.decision.value} ({c.coverage_part.value}), cites {cites}")
    if run.fraud:
        checks = ", ".join(f"{c.name} {c.risk:.2f}" for c in run.fraud.checks)
        print(f"Fraud:    score {run.fraud.score:.2f}  [{checks}]")
    if run.payout:
        print("Payout steps:")
        for s in run.payout.result.steps:
            print(f"  {s.label:<52} {s.amount:>11,.2f}  {s.clause_id or ''}")
    print("Review reasons:", ", ".join(run.review_reasons) or "none")
    if run.status.value == "pending_review":
        print(f"\nWaiting for an adjuster:  python -m claimpilot.cli review {run.claim_id} approve")
    if run.final_payout is not None:
        print(f"Final payout: {run.final_payout:,.2f}")
    if run.letter:
        flag = "  [NEEDS ADJUSTER EDIT]" if run.letter.needs_adjuster_edit else ""
        print(f"\nLetter (revisions: {run.letter.revisions}){flag}\nSubject: {run.letter.subject}")
        print(run.letter.body)
        if run.letter.issues:
            print("Unresolved issues:", *run.letter.issues, sep="\n  - ")
    if timeline:
        print("\nTimeline:", *timeline, sep="\n")


async def main(args: argparse.Namespace) -> None:
    settings = get_settings()
    timeline: list[str] = []
    llm, tools = LLMClient(settings), ToolClient(settings)

    def on_llm(r: CallRecord) -> None:
        err = f"  ERROR {r.error}" if r.error else ""
        timeline.append(
            f"  llm   {r.node:<22} {r.model:<20} {r.latency_ms:>8.0f} ms  "
            f"tokens {r.input_tokens}/{r.output_tokens}  [{r.source}]{err}"
        )

    def on_tool(r: ToolRecord) -> None:
        err = f"  ERROR {r.error}" if r.error else ""
        timeline.append(f"  tool  {r.tool:<22} {'':<20} {r.latency_ms:>8.0f} ms{err}")

    llm.add_listener(on_llm)
    tools.add_listener(on_tool)
    try:
        async with open_checkpointer() as saver:
            service = ClaimService(llm, tools, get_sessionmaker(), saver)
            if args.cmd == "submit":
                form = ClaimForm.model_validate_json(args.form.read_text())
                run = await service.start(form, [(p.name, p.read_bytes()) for p in args.photos])
            elif args.cmd == "review":
                decision = ReviewDecision(
                    action=args.action, reason=args.reason, payout_amount=args.amount
                )
                run = await service.resume(args.claim_id, decision)
            else:
                run = await service.get(args.claim_id)
        if getattr(args, "json", False):
            print(run.model_dump_json(indent=2))
        else:
            print_run(run, timeline)
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit")
    s.add_argument("form", type=Path)
    s.add_argument("photos", type=Path, nargs="*")
    r = sub.add_parser("review")
    r.add_argument("claim_id")
    r.add_argument("action", choices=["approve", "deny", "override"])
    r.add_argument("--reason")
    r.add_argument("--amount", type=float)
    sh = sub.add_parser("show")
    sh.add_argument("claim_id")
    sh.add_argument("--json", action="store_true")
    asyncio.run(main(p.parse_args()))
