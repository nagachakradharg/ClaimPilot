"""Photo severity eval on claim-style damage photos.

Photos are openly licensed (CC BY / BY-SA / CC0, via Openverse; see data/photos/ATTRIBUTION.md)
and hand-labeled low / medium / high; `also_ok` marks borderline photos where the neighbouring
level is also acceptable.

- dev: data/photos/*        used while writing the severity rubric
- val: data/photos/val/*    labeled before any model run on it; never used to tune prompts

    uv run python -m claimpilot.evals.severity --set dev|val [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections import Counter
from datetime import UTC, date, datetime

from claimpilot import tracing
from claimpilot.config import DATA_DIR, EVALS_DIR, get_settings
from claimpilot.context import run_context
from claimpilot.domain import ClaimExtraction, ClaimForm, Severity
from claimpilot.evals.report import write_report
from claimpilot.graphs.intake import NODE, render_messages
from claimpilot.graphs.intake import PROMPT_VERSION as INTAKE_PROMPT_VERSION
from claimpilot.llm import CallRecord, LLMClient
from claimpilot.photos import StoredPhoto

SETS = {"dev": EVALS_DIR / "severity_dev.jsonl", "val": EVALS_DIR / "severity_val.jsonl"}
RESULTS_DIR = EVALS_DIR / "results"
ORDER = [Severity.LOW, Severity.MEDIUM, Severity.HIGH]
# Neutral form: the model must judge severity from the photo, not from the text.
FORM = ClaimForm(
    policy_number="EVAL",
    incident_date=date(2026, 3, 1),
    incident_location="Not stated",
    description="My car was damaged. Please see the attached photo.",
)


def summarize(scored: list[dict], calls: list[CallRecord]) -> dict:
    ok = [s for s in scored if not s["error"]]
    n = len(scored)
    idx = {s: i for i, s in enumerate(ORDER)}
    high = [s for s in ok if s["expected"] == "high"]
    lat = [c.latency_ms for c in calls if c.error is None]
    return {
        "n": n,
        "errors": n - len(ok),
        "accuracy": round(sum(s["predicted"] in s["accepted"] for s in ok) / n, 3),
        "within_one": round(
            sum(abs(idx[Severity(s["expected"])] - idx[Severity(s["predicted"])]) <= 1 for s in ok)
            / n,
            3,
        ),
        "high_recall": round(sum(s["predicted"] == "high" for s in high) / len(high), 3)
        if high
        else None,
        "latency_p50_ms": round(statistics.median(lat), 1) if lat else None,
        "confusion": dict(Counter(f"{s['expected']}->{s['predicted']}" for s in ok)),
    }


async def run(name: str, limit: int | None) -> dict:
    settings = get_settings()
    rows = [json.loads(x) for x in SETS[name].read_text().splitlines() if x.strip()]
    rows = rows[:limit] if limit else rows
    llm, calls = LLMClient(settings), []
    llm.add_listener(calls.append)
    print(
        f"Severity eval ({name}): {len(rows)} photos, mode={settings.llm_mode}, "
        f"model={settings.model_for(NODE)}, prompt={INTAKE_PROMPT_VERSION}"
    )
    scored = []
    session = tracing.eval_session(f"severity-{name}")
    for row in rows:
        photo = StoredPhoto(str(DATA_DIR / row["path"]), "", 0, 0, "image/jpeg")
        accepted = [row["severity"], *row.get("also_ok", [])]
        start = time.perf_counter()
        with (
            run_context(f"eval-sev-{row['image']}"),
            tracing.eval_case(f"severity-{name}", session, row["image"],
                              {"image": row["image"], "form": FORM.model_dump(mode="json")},
                              {"severity": row["severity"], "also_ok": accepted[1:]}) as trace,
        ):  # fmt: skip
            try:
                messages = render_messages(FORM, [photo], settings)
                ex = await llm.structured(NODE, ClaimExtraction, messages)
                pred, err = ex.severity.value, False
            except Exception as exc:
                pred, err = None, True
                print(f"  ERROR {row['image']}: {type(exc).__name__}: {exc}")
            trace.update(output={"severity": pred, "ok": pred in accepted})
        mark = "ok " if pred in accepted else "XX "
        print(
            f"  {mark}{row['image'][:40]:<40} expected={row['severity']:<7} got={pred}  "
            f"{time.perf_counter() - start:5.1f}s"
        )
        scored.append(
            {
                "image": row["image"],
                "expected": row["severity"],
                "accepted": accepted,
                "predicted": pred,
                "error": err,
                "trace_id": trace.trace_id,
            }
        )
    tracing.flush()
    payload = {
        "langfuse_session_id": session if tracing.get_langfuse() else None,
        "suite": f"severity_{name}",
        "prompt_version": INTAKE_PROMPT_VERSION,
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "llm_mode": settings.llm_mode.value,
        "model": settings.model_for(NODE),
        "llm_reasoning": settings.llm_reasoning,
        "summary": summarize(scored, calls),
        "cases": scored,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe = payload["model"].replace(":", "_").replace("/", "_")
    out = RESULTS_DIR / f"severity_{name}_{payload['timestamp'].replace(':', '')}_{safe}.json"
    out.write_text(json.dumps(payload, indent=2))
    write_report()
    print(json.dumps(payload["summary"], indent=2))
    return payload


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--set", choices=sorted(SETS), default="val")
    p.add_argument("--limit", type=int)
    a = p.parse_args()
    asyncio.run(run(a.set, a.limit))


if __name__ == "__main__":
    main()
