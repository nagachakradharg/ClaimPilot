"""Letter eval (amount and decision must match the record in 100% of letters).

Runs the letter subgraph on evals/letter_cases.jsonl and checks every final letter in code:
- decision: approval wording for approvals; clear denial wording and no amounts for denials
- amount: the exact payout appears; no dollar amount other than payout/deductible appears
- leak: no internal review terms (fraud, investigation, ...)
- deductible wording: a deductible amount is described as a deductible
- quote: at least one cited clause is quoted or referenced

"Sendable" letters are those not flagged for adjuster edit; they must pass every check.

    uv run python -m claimpilot.evals.letters [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import time
from datetime import UTC, datetime

from claimpilot import tracing
from claimpilot.config import EVALS_DIR, get_settings
from claimpilot.context import run_context
from claimpilot.evals.report import write_report
from claimpilot.graphs.letter import (
    INTERNAL_TERMS,
    WRITER,
    Decision,
    Letter,
    LetterInput,
    build_letter_graph,
)
from claimpilot.llm import CallRecord, LLMClient
from claimpilot.redact import known_pii

CASES = EVALS_DIR / "letter_cases.jsonl"
RESULTS_DIR = EVALS_DIR / "results"
CHECKS = ("decision", "amount", "no_leak", "deductible_wording", "quotes_clause")


def _money(x: float) -> str:
    return f"${x:,.2f}"


def check_letter(rec: LetterInput, letter: Letter) -> dict[str, bool]:
    text = f"{letter.subject}\n{letter.body}"
    amounts = set(re.findall(r"\$\s?[\d,]+(?:\.\d{2})?", text))
    approved = rec.decision is not Decision.DENIED
    if approved:
        decision_ok = bool(re.search(r"\bapprov", text, re.I)) and not re.search(
            r"\b(denied|deny|not covered)\b", text, re.I
        )
        allowed = {_money(rec.payout)} | (
            {_money(rec.deductible)} if rec.deductible is not None else set()
        )
        amount_ok = _money(rec.payout) in text and amounts <= allowed
    else:
        decision_ok = bool(re.search(r"\b(denied|deny|not covered|unable to)\b", text, re.I))
        amount_ok = not amounts
    deductible_ok = True
    if approved and rec.deductible is not None:
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if _money(rec.deductible) in sentence and "deductible" not in sentence.lower():
                deductible_ok = False
    quotes_ok = not rec.citations or any(
        c.clause_id in text or c.quote[:40].lower() in text.lower() for c in rec.citations
    )
    return {
        "decision": decision_ok,
        "amount": amount_ok,
        "no_leak": not INTERNAL_TERMS.search(text),
        "deductible_wording": deductible_ok,
        "quotes_clause": quotes_ok,
    }


def summarize(scored: list[dict], calls: list[CallRecord]) -> dict:
    ok = [s for s in scored if not s["error"]]
    n = len(scored)
    sendable = [s for s in ok if not s["needs_adjuster_edit"]]
    rate = {k: round(sum(s["checks"][k] for s in ok) / n, 3) for k in CHECKS}
    writer_lat = [c.latency_ms for c in calls if c.node == WRITER and c.error is None]
    return {
        "n": n,
        "errors": n - len(ok),
        **rate,
        "flagged_for_edit": round(sum(s["needs_adjuster_edit"] for s in ok) / n, 3),
        "sendable": len(sendable),
        "sendable_all_checks": round(
            sum(all(s["checks"].values()) for s in sendable) / len(sendable), 3
        )
        if sendable
        else None,
        "avg_revisions": round(statistics.mean(s["revisions"] for s in ok), 2) if ok else None,
        "writer_p50_ms": round(statistics.median(writer_lat), 1) if writer_lat else None,
    }


async def run(limit: int | None) -> dict:
    settings = get_settings()
    cases = [json.loads(x) for x in CASES.read_text().splitlines() if x.strip()]
    cases = cases[:limit] if limit else cases
    llm, calls = LLMClient(settings), []
    llm.add_listener(calls.append)
    graph = build_letter_graph(llm)
    print(f"Letter eval: {len(cases)} cases, mode={settings.llm_mode}, "
          f"model={settings.model_for(WRITER)}")  # fmt: skip
    scored = []
    session = tracing.eval_session("letters")
    for case in cases:
        rec = LetterInput.model_validate(case["record"])
        tracing.register_pii(known_pii(rec.claimant_name))  # mask names in traces, as in the app
        start = time.perf_counter()
        with (
            run_context(f"eval-{case['id']}"),
            tracing.eval_case("letters", session, case["id"], case["record"],
                              {"decision": rec.decision.value, "payout": rec.payout}) as trace,
        ):  # fmt: skip
            try:
                letter: Letter = (
                    await graph.ainvoke({"record": rec}, {"callbacks": tracing.get_callbacks()})
                )["letter"]
                checks = check_letter(rec, letter)
            except Exception as exc:
                print(f"  ERROR {case['id']}: {type(exc).__name__}: {exc}")
                scored.append({"id": case["id"], "error": True})
                continue
            trace.update(output={"subject": letter.subject, "body": letter.body,
                                 "checks": checks, "revisions": letter.revisions})  # fmt: skip
        failed = [k for k, v in checks.items() if not v]
        flag = " [flagged]" if letter.needs_adjuster_edit else ""
        print(f"  {'ok ' if not failed else 'XX '}{case['id']} {rec.decision.value:<13} "
              f"rev={letter.revisions}{flag} failed={failed} "
              f"{time.perf_counter() - start:5.1f}s")  # fmt: skip
        scored.append(
            {
                "id": case["id"],
                "error": False,
                "trace_id": trace.trace_id,
                "checks": checks,
                "revisions": letter.revisions,
                "needs_adjuster_edit": letter.needs_adjuster_edit,
                "issues": letter.issues,
                "subject": letter.subject,
                "body": letter.body,
            }
        )
    tracing.flush()
    payload = {
        "langfuse_session_id": session if tracing.get_langfuse() else None,
        "suite": "letters",
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "llm_mode": settings.llm_mode.value,
        "model": settings.model_for(WRITER),
        "summary": summarize(scored, calls),
        "cases": scored,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe = payload["model"].replace(":", "_").replace("/", "_")
    out = RESULTS_DIR / f"letters_{payload['timestamp'].replace(':', '')}_{safe}.json"
    out.write_text(json.dumps(payload, indent=2))
    write_report()
    print(json.dumps(payload["summary"], indent=2))
    return payload


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int)
    asyncio.run(run(p.parse_args().limit))


if __name__ == "__main__":
    main()
