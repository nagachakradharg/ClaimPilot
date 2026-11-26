"""API end to end with the fake LLM: roles, audit trail, PII redaction."""

import asyncio
import io
import json

import httpx
import pytest
from PIL import Image

from claimpilot.api.main import create_app
from claimpilot.llm import fake_responses
from tests.conftest import requires_db
from tests.test_claim_graph import _fakes

pytestmark = [requires_db, pytest.mark.usefixtures("seeded_db")]
PW = "claimpilot-demo"
CLAIMANT = "casey.fofana@example.com"  # holder of CP-100003 (seeded)
ADJUSTER, AUDITOR = "adjuster@claimpilot.example", "auditor@claimpilot.example"
FORM = {
    "policy_number": "CP-100003",
    "incident_date": "2026-03-04",
    "incident_location": "Austin, TX",
    "description": "Rear-ended at a red light by Casey Fofana's neighbour; bumper pushed in.",
}


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (120, 120, 120)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
async def api():
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app
            yield client


async def _token(api, email: str) -> dict[str, str]:
    r = await api.post("/auth/login", data={"username": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _settle(api) -> None:
    await asyncio.gather(*list(api.app.state.tasks))
    await api.app.state.hub.flush()


async def _submit(api, headers, form=FORM) -> str:
    r = await api.post(
        "/claims",
        headers=headers,
        data={"form": json.dumps(form)},
        files=[("photos", ("rear.jpg", _jpeg(), "image/jpeg"))],
    )
    assert r.status_code == 202, r.text
    return r.json()["claim_id"]


async def test_login_and_me(api):
    assert (
        await api.post("/auth/login", data={"username": ADJUSTER, "password": "x"})
    ).status_code == 401
    h = await _token(api, ADJUSTER)
    me = (await api.get("/auth/me", headers=h)).json()
    assert me["role"] == "adjuster"
    assert (await api.get("/claims")).status_code == 401  # no token


async def test_forbidden_action_per_role(api):
    claimant = await _token(api, CLAIMANT)
    adjuster = await _token(api, ADJUSTER)
    auditor = await _token(api, AUDITOR)
    # adjuster cannot file claims
    r = await api.post("/claims", headers=adjuster, data={"form": json.dumps(FORM)},
                       files=[("photos", ("a.jpg", _jpeg(), "image/jpeg"))])  # fmt: skip
    assert r.status_code == 403
    # claimant cannot claim on someone else's policy
    other = FORM | {"policy_number": "CP-100005"}
    r = await api.post("/claims", headers=claimant, data={"form": json.dumps(other)},
                       files=[("photos", ("a.jpg", _jpeg(), "image/jpeg"))])  # fmt: skip
    assert r.status_code == 403
    # claimant and auditor cannot review; claimant cannot read audit trails or metrics
    review = {"action": "approve"}
    assert (await api.post("/claims/x/review", headers=claimant, json=review)).status_code == 403
    assert (await api.post("/claims/x/review", headers=auditor, json=review)).status_code == 403
    assert (await api.get("/claims/x/audit", headers=claimant)).status_code == 403
    assert (await api.get("/metrics", headers=claimant)).status_code == 403


async def test_claimant_cannot_see_another_claimants_claim(api):
    from sqlalchemy import select

    from claimpilot.db import get_sessionmaker
    from claimpilot.models import User

    _fakes()
    claim_id = await _submit(api, await _token(api, CLAIMANT))
    await _settle(api)
    async with get_sessionmaker()() as session:
        other_email = await session.scalar(
            select(User.email).where(User.policy_number == "CP-100005")
        )
    other = await _token(api, other_email)
    assert (await api.get(f"/claims/{claim_id}", headers=other)).status_code == 404
    assert claim_id not in {c["claim_id"] for c in (await api.get("/claims", headers=other)).json()}


async def test_auto_approved_flow_views_audit_and_metrics(api):
    _fakes()
    claimant, adjuster = await _token(api, CLAIMANT), await _token(api, ADJUSTER)
    claim_id = await _submit(api, claimant)
    await _settle(api)

    mine = (await api.get(f"/claims/{claim_id}", headers=claimant)).json()
    assert mine["status"] == "closed" and mine["decision"] == "auto_approved"
    assert mine["letter"]["body"] and "detail" not in mine and "review_reasons" not in mine

    staff = (await api.get(f"/claims/{claim_id}", headers=adjuster)).json()
    assert staff["detail"]["fraud"]["checks"] and staff["detail"]["payout"]["result"]["steps"]

    audit = (await api.get(f"/claims/{claim_id}/audit", headers=adjuster)).json()
    kinds = {e["kind"] for e in audit}
    assert {"status", "node_start", "node_end", "llm", "tool"} <= kinds
    assert [e["ts"] for e in audit] == sorted(e["ts"] for e in audit)  # ordered history
    names = {e["name"] for e in audit}
    assert {"fraud/photo_match", "coverage/assess", "letter/write"} <= names
    blob = json.dumps(audit)
    assert "Fofana" not in blob and "casey.fofana@example.com" not in blob

    m = (await api.get("/metrics", headers=adjuster)).json()
    assert m["claims_total"] >= 1 and m["straight_through_rate"] == 1.0
    assert m["manual_handling_minutes_assumption"] > 0


async def test_review_flow_through_api(api):
    _fakes(photo={"observations": [], "mismatches": ["no damage visible"], "risk": 0.9})
    claimant, adjuster = await _token(api, CLAIMANT), await _token(api, ADJUSTER)
    claim_id = await _submit(api, claimant)
    await _settle(api)
    queue = (await api.get("/claims?status_filter=pending_review", headers=adjuster)).json()
    assert claim_id in {c["claim_id"] for c in queue}
    mine = (await api.get(f"/claims/{claim_id}", headers=claimant)).json()
    assert mine["letter"] is None  # nothing shown before a human decides

    bad = await api.post(f"/claims/{claim_id}/review", headers=adjuster, json={"action": "deny"})
    assert bad.status_code == 422  # deny needs a reason
    r = await api.post(f"/claims/{claim_id}/review", headers=adjuster, json={"action": "approve"})
    assert r.status_code == 202
    await _settle(api)
    done = (await api.get(f"/claims/{claim_id}", headers=adjuster)).json()
    assert done["status"] == "closed" and done["decision"] == "approved"
    again = await api.post(
        f"/claims/{claim_id}/review", headers=adjuster, json={"action": "approve"}
    )
    assert again.status_code == 409
    audit = (await api.get(f"/claims/{claim_id}/audit", headers=adjuster)).json()
    human = [e for e in audit if e["kind"] == "human"]
    assert human and human[0]["actor"] == ADJUSTER


async def test_sse_stream_for_finished_claim(api):
    _fakes()
    claimant = await _token(api, CLAIMANT)
    claim_id = await _submit(api, claimant)
    await _settle(api)
    async with api.stream("GET", f"/claims/{claim_id}/events", headers=claimant) as r:
        assert r.status_code == 200
        body = "".join([chunk async for chunk in r.aiter_text()])
    assert "event: status" in body and "closed" in body


async def test_invalid_submission_reported_to_claimant(api):
    fake_responses.clear()  # no fakes: an LLM call would raise, proving none happened
    claimant = await _token(api, CLAIMANT)
    r = await api.post("/claims", headers=claimant, data={"form": json.dumps(FORM)},
                       files=[("photos", ("x.gif", b"GIF89a....", "image/gif"))])  # fmt: skip
    claim_id = r.json()["claim_id"]
    await _settle(api)
    mine = (await api.get(f"/claims/{claim_id}", headers=claimant)).json()
    assert mine["status"] == "invalid" and mine["errors"]


async def test_photos_served_reencoded_and_scoped(api):
    _fakes()
    claimant = await _token(api, CLAIMANT)
    claim_id = await _submit(api, claimant)
    await _settle(api)
    names = (await api.get(f"/claims/{claim_id}/photos", headers=claimant)).json()
    assert len(names) == 1
    r = await api.get(f"/claims/{claim_id}/photos/{names[0]}", headers=claimant)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8"
    # staff can see it; path traversal and other claimants cannot
    adjuster = await _token(api, ADJUSTER)
    assert (
        await api.get(f"/claims/{claim_id}/photos/{names[0]}", headers=adjuster)
    ).status_code == 200
    assert (
        await api.get(f"/claims/{claim_id}/photos/..%2F..%2Fsecret", headers=adjuster)
    ).status_code == 404
    from sqlalchemy import select

    from claimpilot.db import get_sessionmaker
    from claimpilot.models import User

    async with get_sessionmaker()() as session:
        other = await session.scalar(select(User.email).where(User.policy_number == "CP-100005"))
    other_h = await _token(api, other)
    assert (await api.get(f"/claims/{claim_id}/photos", headers=other_h)).status_code == 404


async def test_flagged_letter_edited_by_adjuster(api):
    _fakes(letter="Subject: Claim {CLAIM_NUMBER}\n\nDear {CLAIMANT_NAME}, we will pay $999.")
    claimant, adjuster = await _token(api, CLAIMANT), await _token(api, ADJUSTER)
    claim_id = await _submit(api, claimant)
    await _settle(api)
    staff = (await api.get(f"/claims/{claim_id}", headers=adjuster)).json()
    assert staff["status"] == "pending_letter"
    mine = (await api.get(f"/claims/{claim_id}", headers=claimant)).json()
    assert mine["letter"] is None  # a flagged letter is never shown to the claimant
    body = {"subject": "Your claim is approved", "body": "Dear Casey, approved. " * 5}
    assert (
        await api.post(f"/claims/{claim_id}/letter", headers=claimant, json=body)
    ).status_code == 403
    r = await api.post(f"/claims/{claim_id}/letter", headers=adjuster, json=body)
    assert r.status_code == 202
    await _settle(api)
    mine = (await api.get(f"/claims/{claim_id}", headers=claimant)).json()
    assert mine["status"] == "closed" and mine["letter"]["subject"] == "Your claim is approved"
    again = await api.post(f"/claims/{claim_id}/letter", headers=adjuster, json=body)
    assert again.status_code == 409


async def test_per_adjuster_queue_and_assignment(api):
    _fakes(photo={"observations": [], "mismatches": ["no damage visible"], "risk": 0.9})
    claimant = await _token(api, CLAIMANT)
    alex, sam = await _token(api, ADJUSTER), await _token(api, "adjuster2@claimpilot.example")
    claim_id = await _submit(api, claimant)
    await _settle(api)
    unassigned = (await api.get("/claims?assigned=none", headers=alex)).json()
    assert claim_id in {c["claim_id"] for c in unassigned}

    r = await api.post(f"/claims/{claim_id}/assign", headers=alex, json={"to": "me"})
    assert r.json()["assigned_to"] == ADJUSTER
    mine = (await api.get("/claims?assigned=me", headers=alex)).json()
    assert [c["claim_id"] for c in mine] == [claim_id]
    assert (await api.get("/claims?assigned=me", headers=sam)).json() == []

    # Sam can neither take it nor act on it; Cool can
    assert (
        await api.post(f"/claims/{claim_id}/assign", headers=sam, json={"to": "me"})
    ).status_code == 409
    review = {"action": "approve"}
    assert (
        await api.post(f"/claims/{claim_id}/review", headers=sam, json=review)
    ).status_code == 409
    assert (
        await api.post(f"/claims/{claim_id}/review", headers=alex, json=review)
    ).status_code == 202
    await _settle(api)
    audit = (await api.get(f"/claims/{claim_id}/audit", headers=alex)).json()
    assert any(e["name"] == "assigned" and e["actor"] == ADJUSTER for e in audit)


async def test_acting_on_unassigned_claim_assigns_it(api):
    _fakes(photo={"observations": [], "mismatches": ["no damage visible"], "risk": 0.9})
    claimant, sam = await _token(api, CLAIMANT), await _token(api, "adjuster2@claimpilot.example")
    claim_id = await _submit(api, claimant)
    await _settle(api)
    r = await api.post(f"/claims/{claim_id}/review", headers=sam, json={"action": "approve"})
    assert r.status_code == 202
    await _settle(api)
    row = (await api.get(f"/claims/{claim_id}", headers=sam)).json()
    assert row["assigned_to"] == "adjuster2@claimpilot.example"
