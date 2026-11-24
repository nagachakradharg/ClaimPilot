#!/usr/bin/env bash
# End-to-end smoke test against a running stack (default: docker compose on :8080, fake LLM).
# Logs in, files a claim with a photo, waits for it to settle, checks the audit trail.
set -euo pipefail
BASE="${BASE:-http://localhost:8080/api}"
PW="${DEMO_PASSWORD:-claimpilot-demo}"
PHOTO="${PHOTO:-data/photos/collision-rear-corner-minor.jpg}"

token() {
  curl -fsS -X POST "$BASE/auth/login" --form-string "username=$1" --form-string "password=$PW" |
    python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'
}

for i in $(seq 1 60); do curl -fsS "$BASE/health" >/dev/null 2>&1 && break; sleep 2; done
curl -fsS "$BASE/health" | python3 -c 'import sys,json;d=json.load(sys.stdin);assert d["database"]["ok"],d;print("health ok, llm_mode", d["llm_mode"])'

CT=$(token casey.fofana@example.com)
AT=$(token adjuster@claimpilot.example)
ID=$(curl -fsS -X POST "$BASE/claims" -H "Authorization: Bearer $CT" \
  --form-string 'form={"policy_number":"CP-100003","incident_date":"2026-03-04","incident_location":"Austin, TX","description":"Rear-ended at a red light; bumper pushed in."}' \
  -F "photos=@$PHOTO;type=image/jpeg" | python3 -c 'import sys,json;print(json.load(sys.stdin)["claim_id"])')
echo "submitted $ID"

for i in $(seq 1 90); do
  STATUS=$(curl -fsS "$BASE/claims/$ID" -H "Authorization: Bearer $AT" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  case "$STATUS" in processing|submitted) sleep 2 ;; *) break ;; esac
done
echo "status $STATUS"
case "$STATUS" in closed|pending_review) ;; *) echo "unexpected status"; exit 1 ;; esac

curl -fsS "$BASE/claims/$ID/audit" -H "Authorization: Bearer $AT" | python3 -c '
import sys, json
events = json.load(sys.stdin)
kinds = {e["kind"] for e in events}
assert {"status", "node_start", "node_end", "llm", "tool"} <= kinds, kinds
blob = json.dumps(events)
assert "Fofana" not in blob and "casey.fofana@example.com" not in blob, "PII in audit trail"
print(f"audit ok: {len(events)} events, PII redacted")'
echo "smoke test passed"
