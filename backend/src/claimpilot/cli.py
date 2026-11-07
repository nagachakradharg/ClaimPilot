"""Run one claim end to end from the command line (week-2 milestone).

    uv run python -m claimpilot.cli ../data/claims/demo-001/form.json ../data/claims/demo-001/*.jpg

Prints the outcome plus a timeline of every model and tool call. Uses LLM_MODE from .env
(default `fake`, which uses backend/fixtures/llm/fake/*.json).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from claimpilot.config import get_settings
from claimpilot.db import get_engine, get_sessionmaker
from claimpilot.domain import ClaimForm
from claimpilot.graphs.claim import run_claim
from claimpilot.llm import CallRecord, LLMClient
from claimpilot.tools import ToolClient, ToolRecord


async def main(form_path: Path, photo_paths: list[Path], as_json: bool) -> None:
    settings = get_settings()
    form = ClaimForm.model_validate_json(form_path.read_text())
    photos = [(p.name, p.read_bytes()) for p in photo_paths]

    timeline: list[str] = []
    llm, tools = LLMClient(settings), ToolClient(settings)

    def on_llm(r: CallRecord) -> None:
        err = f"  ERROR {r.error}" if r.error else ""
        timeline.append(
            f"  llm   {r.node:<20} {r.model:<24} {r.latency_ms:>9.0f} ms  "
            f"tokens {r.input_tokens}/{r.output_tokens}  [{r.source}]{err}"
        )

    def on_tool(r: ToolRecord) -> None:
        err = f"  ERROR {r.error}" if r.error else ""
        timeline.append(f"  tool  {r.tool:<20} {'':<24} {r.latency_ms:>9.0f} ms{err}")

    llm.add_listener(on_llm)
    tools.add_listener(on_tool)

    run = await run_claim(form, photos, llm, tools, get_sessionmaker())
    await get_engine().dispose()

    if as_json:
        print(run.model_dump_json(indent=2))
        return
    print(f"\nClaim {run.claim_id}  ->  {run.status.value.upper()}   (core: {run.core_claim_id})")
    if run.error:
        print(f"Error: {run.error}")
    if run.intake and run.intake.errors:
        print("Rejected:", *run.intake.errors, sep="\n  - ")
    if run.intake and run.intake.extraction:
        ex = run.intake.extraction
        print(
            f"Intake: {ex.claim_type.value}, severity {ex.severity.value}, areas {ex.damage_areas}"
        )
    if run.coverage:
        c = run.coverage
        print(
            f"Coverage: {c.decision.value} ({c.coverage_part.value}), cites "
            f"{[x.clause_id for x in c.citations]}"
        )
    if run.payout:
        print("Payout steps:")
        for step in run.payout.result.steps:
            print(f"  {step.label:<52} {step.amount:>11,.2f}  {step.clause_id or ''}")
    print("Review reasons:", json.dumps(run.review_reasons) if run.review_reasons else "none")
    print("Timeline:", *timeline, sep="\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("form", type=Path)
    p.add_argument("photos", type=Path, nargs="*")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    asyncio.run(main(a.form, a.photos, a.json))
