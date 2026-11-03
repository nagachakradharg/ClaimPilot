"""Coverage eval. Runs every case in evals/coverage.jsonl through the
coverage subgraph and reports decision accuracy, safe-escalation recall, citation correctness
and latency/tokens. Results go to evals/results/*.json; evals/report.md is regenerated.

    uv run python -m claimpilot.evals.coverage [--limit N] [--concurrency 1] [--ids cov-001,...]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claimpilot.config import EVALS_DIR, get_settings
from claimpilot.context import run_context
from claimpilot.db import get_sessionmaker
from claimpilot.domain import ClaimFacts, CoverageDecision
from claimpilot.evals.report import write_report
from claimpilot.graphs.coverage import NODE, run_coverage
from claimpilot.llm import CallRecord, LLMClient

DATASET = EVALS_DIR / "coverage.jsonl"
RESULTS_DIR = EVALS_DIR / "results"
REVIEW = {CoverageDecision.NOT_COVERED.value, CoverageDecision.AMBIGUOUS.value}


def load_cases(path: Path = DATASET) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def score_case(case: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    exp = case["expected"]
    if result is None:
        return {"id": case["id"], "error": True, "decision_ok": False, "escalated": True}
    cited = {c["clause_id"] for c in result["citations"]}
    must = set(exp["must_cite"])
    acceptable = set(exp["acceptable_citations"])
    return {
        "id": case["id"],
        "expected": exp["decision"],
        "predicted": result["decision"],
        "decision_ok": result["decision"] == exp["decision"],
        "part_ok": result["coverage_part"] == exp["coverage_part"],
        "escalated": result["decision"] in REVIEW,
        "should_escalate": exp["decision"] in REVIEW,
        # Citation correctness: the decisive clause(s) are cited, nothing outside the allowed set
        "citation_ok": must <= cited and cited <= acceptable if must else cited <= acceptable,
        "must_cite_hit": must <= cited,
        "cited": sorted(cited),
        "flags": result["flags"],
        "error": False,
    }


def summarize(scored: list[dict[str, Any]], calls: list[CallRecord]) -> dict[str, Any]:
    n = len(scored)
    should = [s for s in scored if s.get("should_escalate")]
    with_must = [s for s in scored if not s["error"] and s["id"]]
    latencies = [c.latency_ms for c in calls if c.node == NODE and c.error is None]
    in_tok = [c.input_tokens or 0 for c in calls if c.node == NODE]
    out_tok = [c.output_tokens or 0 for c in calls if c.node == NODE]
    return {
        "n": n,
        "errors": sum(s["error"] for s in scored),
        "decision_accuracy": round(sum(s["decision_ok"] for s in scored) / n, 3),
        "escalation_recall": round(sum(s["escalated"] for s in should) / len(should), 3)
        if should
        else None,
        "false_auto_approvals": sum(
            1 for s in scored if s.get("should_escalate") and not s["escalated"]
        ),
        "citation_correctness": round(
            sum(s.get("citation_ok", False) for s in with_must) / max(len(with_must), 1), 3
        ),
        "must_cite_recall": round(
            sum(s.get("must_cite_hit", False) for s in with_must) / max(len(with_must), 1), 3
        ),
        "llm_calls": len(latencies),
        "latency_p50_ms": round(statistics.median(latencies), 1) if latencies else None,
        "latency_p95_ms": round(statistics.quantiles(latencies, n=20, method="inclusive")[-1], 1)
        if len(latencies) >= 2
        else None,
        "avg_input_tokens": round(statistics.mean(in_tok)) if in_tok else None,
        "avg_output_tokens": round(statistics.mean(out_tok)) if out_tok else None,
        "confusion": dict(
            Counter(f"{s.get('expected')}->{s.get('predicted')}" for s in scored if not s["error"])
        ),
    }


async def run(limit: int | None, concurrency: int, ids: list[str] | None) -> dict[str, Any]:
    settings = get_settings()
    cases = load_cases()
    if ids:
        cases = [c for c in cases if c["id"] in ids]
    cases = cases[:limit] if limit else cases

    llm = LLMClient(settings)
    calls: list[CallRecord] = []
    llm.add_listener(calls.append)
    sessionmaker = get_sessionmaker()
    sem = asyncio.Semaphore(concurrency)

    async def one(case: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            facts = ClaimFacts.model_validate(case["facts"])
            start = time.perf_counter()
            with run_context(f"eval-{case['id']}"):
                try:
                    result = (await run_coverage(facts, llm, sessionmaker)).model_dump(mode="json")
                except Exception as exc:  # keep going; errors count as misses
                    print(f"  {case['id']}: ERROR {type(exc).__name__}: {exc}")
                    return score_case(case, None)
            scored = score_case(case, result)
            mark = "ok " if scored["decision_ok"] else "XX "
            print(
                f"  {mark}{case['id']} expected={scored['expected']:<12} "
                f"got={scored['predicted']:<12} cited={scored['cited']} "
                f"{time.perf_counter() - start:5.1f}s"
            )
            return scored

    print(
        f"Coverage eval: {len(cases)} cases, mode={settings.llm_mode}, "
        f"model={settings.model_for(NODE)}"
    )
    scored = await asyncio.gather(*(one(c) for c in cases))
    summary = summarize(list(scored), calls)
    payload = {
        "suite": "coverage",
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "llm_mode": settings.llm_mode.value,
        "model": settings.model_for(NODE),
        "embedding": f"{settings.embedding_provider}:{settings.embedding_model}",
        "summary": summary,
        "cases": list(scored),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_model = payload["model"].replace(":", "_").replace("/", "_")
    out = RESULTS_DIR / f"coverage_{payload['timestamp'].replace(':', '')}_{safe_model}.json"
    out.write_text(json.dumps(payload, indent=2))
    write_report()
    print(json.dumps(summary, indent=2))
    print(f"Saved {out.relative_to(EVALS_DIR.parent)}")
    return payload


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int)
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--ids", type=lambda s: s.split(","))
    args = p.parse_args()
    asyncio.run(run(args.limit, args.concurrency, args.ids))


if __name__ == "__main__":
    main()
