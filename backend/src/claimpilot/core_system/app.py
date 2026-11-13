"""Mock legacy "Core Claims System" (CCS).

Stands in for the insurer's system of record. It deliberately looks legacy: uppercase field
codes, amounts as strings, status codes, and optional injected 503s (CORE_FAULT_RATE) so the
integration layer has real mapping and retry work to do. ClaimPilot never calls it directly;
only the MCP server does (anti-corruption layer).

Run standalone:  uv run uvicorn claimpilot.core_system.app:app --port 8100
"""

from __future__ import annotations

import json
import os
import random
from datetime import UTC, date, datetime
from itertools import count
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from claimpilot.config import DATA_DIR, REPO_DIR

VALUATIONS_FILE = DATA_DIR / "core_system" / "valuations.json"
# The system of record persists its claims (runtime data, git-ignored under storage/).
CLAIMS_FILE = Path(
    os.getenv("CORE_CLAIMS_FILE", REPO_DIR / "storage" / "core_system" / "claims.json")
)
STATUS_CODES = {"OPEN", "PROC", "PEND_REV", "AUTO_APPRVD", "APPRVD", "DENIED", "CLOSED", "FAILED"}
LOSS_TYPE_CODES = {"COLL", "THFT", "GLSS", "WTHR", "OTHR"}


class ClaimCreate(BaseModel):
    POLICY_NO: str
    LOSS_DT: date
    LOSS_TYPE_CD: str
    LOSS_DESC: str = Field(max_length=2000)
    CLMD_AMT: str | None = None  # legacy: amounts are strings
    EXT_REF: str | None = None  # caller's id (ClaimPilot claim/run id)


class StatusUpdate(BaseModel):
    STATUS_CD: str
    NOTE_TXT: str | None = None


def _load_valuations(path: Path) -> dict[str, dict]:
    return json.loads(path.read_text()) if path.exists() else {}


def create_core_app(
    fault_rate: float | None = None,
    seed: int | None = None,
    fail_first: int = 0,
    claims_file: Path | None = CLAIMS_FILE,
) -> FastAPI:
    """`claims_file=None` keeps claims in memory only (tests)."""
    app = FastAPI(title="Core Claims System (mock legacy)", version="7.4-mock")
    rng = random.Random(seed)
    rate = float(os.getenv("CORE_FAULT_RATE", "0")) if fault_rate is None else fault_rate
    claims: dict[str, dict] = (
        json.loads(claims_file.read_text()) if claims_file and claims_file.exists() else {}
    )
    seq = count(len(claims) + 1)

    def persist() -> None:
        if claims_file:
            claims_file.parent.mkdir(parents=True, exist_ok=True)
            claims_file.write_text(json.dumps(claims, indent=1))

    pending_failures = [fail_first]  # deterministic faults for tests
    valuations = _load_valuations(VALUATIONS_FILE)

    @app.middleware("http")
    async def inject_faults(request: Request, call_next):
        is_api = request.url.path.startswith("/api/")
        forced = is_api and pending_failures[0] > 0
        if forced:
            pending_failures[0] -= 1
        if forced or (rate and is_api and rng.random() < rate):
            from fastapi.responses import JSONResponse

            return JSONResponse({"ERR_CD": "SVC_UNAVAIL"}, status_code=503)
        return await call_next(request)

    @app.get("/api/v1/vehicles/{vin}/valuation")
    async def valuation(vin: str) -> dict:
        v = valuations.get(vin)
        if not v:
            raise HTTPException(404, {"ERR_CD": "VIN_NOT_FOUND"})
        return {
            "VIN": vin,
            "ACV_AMT": f"{v['acv']:.2f}",
            "VAL_SRC_CD": "MKTGUIDE",
            "VAL_DT": date.today().isoformat(),
        }

    @app.post("/api/v1/claims", status_code=201)
    async def create_claim(body: ClaimCreate) -> dict:
        if body.LOSS_TYPE_CD not in LOSS_TYPE_CODES:
            raise HTTPException(422, {"ERR_CD": "BAD_LOSS_TYPE"})
        if body.EXT_REF:  # idempotent on caller reference (safe to retry)
            for rec in claims.values():
                if rec["EXT_REF"] == body.EXT_REF:
                    return rec
        claim_no = f"CCS-{datetime.now(UTC).year}-{next(seq):06d}"
        rec = body.model_dump(mode="json") | {
            "CLAIM_NO": claim_no,
            "STATUS_CD": "OPEN",
            "CRT_TS": datetime.now(UTC).isoformat(timespec="seconds"),
            "HIST": [],
        }
        claims[claim_no] = rec
        persist()
        return rec

    @app.get("/api/v1/claims/{claim_no}")
    async def get_claim(claim_no: str) -> dict:
        if claim_no not in claims:
            raise HTTPException(404, {"ERR_CD": "CLM_NOT_FOUND"})
        return claims[claim_no]

    @app.put("/api/v1/claims/{claim_no}/status")
    async def update_status(claim_no: str, body: StatusUpdate) -> dict:
        if claim_no not in claims:
            raise HTTPException(404, {"ERR_CD": "CLM_NOT_FOUND"})
        if body.STATUS_CD not in STATUS_CODES:
            raise HTTPException(422, {"ERR_CD": "BAD_STATUS_CD"})
        rec = claims[claim_no]
        rec["HIST"].append(
            {
                "FROM": rec["STATUS_CD"],
                "TO": body.STATUS_CD,
                "NOTE": body.NOTE_TXT,
                "TS": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        rec["STATUS_CD"] = body.STATUS_CD
        persist()
        return rec

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "vehicles": len(valuations), "claims": len(claims)}

    return app


app = create_core_app()
