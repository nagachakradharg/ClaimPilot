"""Seed the database: synthetic policies, claim-history profiles, and policy-form clauses.

Usage:  uv run python -m claimpilot.seed

Vehicles, deductibles and history signals are mapped from the CC0 Kaggle "Vehicle Claim Fraud
Detection" dataset (data/raw/fraud_oracle.csv) when present; otherwise a synthetic fallback is
used so CI and fresh clones still work. Names/contacts are fictional (example.com, 555 numbers).
"""

from __future__ import annotations

import asyncio
import csv
import json
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import delete

from claimpilot.config import DATA_DIR
from claimpilot.db import get_sessionmaker
from claimpilot.models import ClaimHistoryProfile, Policy
from claimpilot.rag import ingest_policy_form

KAGGLE_CSV = DATA_DIR / "raw" / "fraud_oracle.csv"
POLICY_FORMS_DIR = DATA_DIR / "policies"
N_POLICIES = 24
LIABILITY_ONLY = {"CP-100019", "CP-100020", "CP-100021", "CP-100022"}
CANCELLED = {"CP-100023"}
EXPIRED = {"CP-100024"}

FIRST = [
    "Avery",
    "Jordan",
    "Riley",
    "Casey",
    "Morgan",
    "Quinn",
    "Rowan",
    "Skyler",
    "Emerson",
    "Hayden",
    "Reese",
    "Parker",
    "Dakota",
    "Sage",
    "Finley",
    "Harper",
]
LAST = [
    "Alvarez",
    "Bennett",
    "Chen",
    "Dubois",
    "Eriksen",
    "Fofana",
    "Garcia",
    "Haddad",
    "Ivanova",
    "Jensen",
    "Kowalski",
    "Lindqvist",
    "Moreau",
    "Nakamura",
    "Okafor",
    "Patel",
]
STREETS = ["Maple Ave", "Oak St", "Harbor Rd", "Cedar Ln", "Elm Ct", "Lakeview Dr"]
CITIES = [("Columbus", "OH"), ("Austin", "TX"), ("Denver", "CO"), ("Raleigh", "NC")]
MODELS = {
    "Honda": "Civic",
    "Toyota": "Camry",
    "Pontiac": "Vibe",
    "Mazda": "Mazda3",
    "Chevrolet": "Malibu",
    "Ford": "Focus",
    "VW": "Jetta",
    "Accura": "TLX",
    "Dodge": "Charger",
    "Mercury": "Milan",
    "Saturn": "Aura",
    "Nisson": "Altima",
    "Saab": "9-3",
    "BMW": "330i",
    "Mecedes": "C300",
    "Jaguar": "XF",
    "Porche": "Cayman",
    "Ferrari": "Roma",
    "Lexus": "ES",
}
MAKE_FIX = {
    "Accura": "Acura",
    "Nisson": "Nissan",
    "Mecedes": "Mercedes-Benz",
    "Porche": "Porsche",
}  # typos in the source data
PRICE_MID = {
    "less than 20000": 17000,
    "20000 to 29000": 24500,
    "30000 to 39000": 34500,
    "40000 to 59000": 49500,
    "60000 to 69000": 64500,
    "more than 69000": 75000,
}
AGE_YEARS = {
    "new": 0,
    "2 years": 2,
    "3 years": 3,
    "4 years": 4,
    "5 years": 5,
    "6 years": 6,
    "7 years": 7,
    "more than 7": 9,
}
PAST_CLAIMS = {"none": 0, "1": 1, "2 to 4": 3, "more than 4": 5}
DAYS_TO_ACCIDENT = {"none": 0, "1 to 7": 4, "8 to 15": 11, "15 to 30": 22, "more than 30": 180}


@dataclass
class SourceRow:
    index: int
    make: str
    category: str
    acv: float
    deductible: float
    past_claims: int
    days_policy_to_accident: int
    address_change_recent: bool
    police_report_filed: bool
    witness_present: bool
    fraud: bool


def _acv(price_band: str, age_band: str) -> float:
    years = AGE_YEARS.get(age_band, 5)
    return round(PRICE_MID.get(price_band, 24500) * (0.85**years), -2)


def load_kaggle_rows(path: Path, rng: random.Random, n: int) -> list[SourceRow]:
    """Stratified sample: ~25% fraud-labeled rows so fraud evals have positives."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(enumerate(csv.DictReader(f)))
    fraud = [r for r in rows if r[1]["FraudFound_P"] == "1"]
    legit = [r for r in rows if r[1]["FraudFound_P"] == "0"]
    n_fraud = n // 4
    picked = rng.sample(fraud, n_fraud) + rng.sample(legit, n - n_fraud)
    rng.shuffle(picked)
    return [
        SourceRow(
            index=i,
            make=r["Make"],
            category=r["VehicleCategory"],
            acv=_acv(r["VehiclePrice"], r["AgeOfVehicle"]),
            deductible=float(r["Deductible"]),
            past_claims=PAST_CLAIMS.get(r["PastNumberOfClaims"], 0),
            days_policy_to_accident=DAYS_TO_ACCIDENT.get(r["Days_Policy_Accident"], 180),
            address_change_recent=r["AddressChange_Claim"] == "under 6 months",
            police_report_filed=r["PoliceReportFiled"] == "Yes",
            witness_present=r["WitnessPresent"] == "Yes",
            fraud=r["FraudFound_P"] == "1",
        )
        for i, r in picked
    ]


def synthetic_rows(rng: random.Random, n: int) -> list[SourceRow]:
    makes = list(MODELS)
    return [
        SourceRow(
            index=-1,
            make=rng.choice(makes),
            category="Sedan",
            acv=float(rng.randrange(6000, 40000, 500)),
            deductible=float(rng.choice([400, 500, 700])),
            past_claims=rng.choice([0, 0, 0, 1, 3]),
            days_policy_to_accident=rng.choice([180, 180, 180, 22, 4]),
            address_change_recent=rng.random() < 0.1,
            police_report_filed=rng.random() < 0.3,
            witness_present=rng.random() < 0.3,
            fraud=i % 4 == 0,
        )
        for i in range(n)
    ]


def build_policies(
    rows: list[SourceRow], rng: random.Random
) -> list[tuple[Policy, ClaimHistoryProfile]]:
    out = []
    for i, src in enumerate(rows, start=1):
        number = f"CP-{100000 + i}"
        first, last = FIRST[i % len(FIRST)], LAST[(i * 7) % len(LAST)]
        city, state = CITIES[i % len(CITIES)]
        make = MAKE_FIX.get(src.make, src.make)
        year = 2026 - rng.randint(1, 9)
        liability_only = number in LIABILITY_ONLY
        effective, expiry = date(2026, 1, 1), date(2026, 12, 31)
        if number in EXPIRED:
            effective, expiry = date(2025, 1, 1), date(2025, 12, 31)
        policy = Policy(
            policy_number=number,
            form_id="PAP-LIAB-01" if liability_only else "PAP-STD-01",
            holder_name=f"{first} {last}",
            holder_email=f"{first.lower()}.{last.lower()}@example.com",
            holder_phone=f"555-01{i:02d}",
            holder_address=f"{100 + i * 11} {STREETS[i % len(STREETS)]}, {city}, {state}",
            effective_date=effective,
            expiry_date=expiry,
            status="cancelled" if number in CANCELLED else "active",
            vehicle={
                "year": year,
                "make": make,
                "model": MODELS.get(src.make, "Sedan"),
                "vin": f"SYNTH{i:012d}",
                "category": src.category,
            },
            vehicle_acv=src.acv,
            collision_deductible=None if liability_only else src.deductible,
            comprehensive_deductible=None if liability_only else max(250.0, src.deductible - 250),
        )
        history = ClaimHistoryProfile(
            policy_number=number,
            past_claims=src.past_claims,
            days_policy_to_accident=src.days_policy_to_accident,
            address_change_recent=src.address_change_recent,
            police_report_filed=src.police_report_filed,
            witness_present=src.witness_present,
            fraud_label=src.fraud,
            source_row=src.index,
        )
        out.append((policy, history))
    return out


def export_core_valuations(policies: list[Policy]) -> None:
    """The mock core system gets its own vehicle-valuation extract (it never reads our DB)."""
    from claimpilot.core_system.app import VALUATIONS_FILE

    VALUATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {p.vehicle["vin"]: {"acv": p.vehicle_acv} for p in policies}
    VALUATIONS_FILE.write_text(json.dumps(data, indent=2, sort_keys=True))


async def seed() -> dict[str, int | str]:
    rng = random.Random(42)
    if KAGGLE_CSV.exists():
        rows, source = load_kaggle_rows(KAGGLE_CSV, rng, N_POLICIES), "kaggle_cc0"
    else:
        rows, source = synthetic_rows(rng, N_POLICIES), "synthetic_fallback"

    built = build_policies(rows, rng)
    export_core_valuations([p for p, _ in built])

    async with get_sessionmaker()() as session, session.begin():
        await session.execute(delete(ClaimHistoryProfile))
        await session.execute(delete(Policy))
        for policy, history in built:
            session.add(policy)
            await session.flush()
            session.add(history)
        n_clauses = 0
        for path in sorted(POLICY_FORMS_DIR.glob("*.md")):
            n_clauses += len(await ingest_policy_form(session, path))
    return {"policies": N_POLICIES, "clauses": n_clauses, "history_source": source}


if __name__ == "__main__":
    print(asyncio.run(seed()))
