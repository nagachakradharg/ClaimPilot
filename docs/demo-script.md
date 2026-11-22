# 2-minute demo video script

Business problem first (requirements §9). Record at 1440×900 in the light theme, with the Docker stack running in `LLM_MODE=live`. Pre-submit one claim that will go to review (mismatched story) so you don't wait on camera; submit the clean claim live.

| Time | Screen | Say (roughly) |
|---|---|---|
| 0:00–0:15 | Title slide or the README header | "Auto claim handlers spend most of each claim on routine reading and lookups. Most claims are simple; a few are risky. ClaimPilot closes the simple ones and gives adjusters the evidence on the rest." |
| 0:15–0:35 | Claimant → New claim: fill the form, attach the rear-corner photo, submit | "A claimant files a claim with photos. Behind this, LangGraph agents read the text and the photos, check the policy and run fraud checks in parallel." |
| 0:35–0:50 | Claimant progress page ticking through the steps | "Progress streams live. Payout is calculated in code, not by the model, and the claim is auto-approved because it's covered, low-risk and under the limit." |
| 0:50–1:05 | Decision letter | "The letter is drafted, independently reviewed and revised once; the amounts are filled in by code." |
| 1:05–1:30 | Adjuster → Queue → the pre-submitted mismatched claim | "This one was sent to a human: the story says a small scratch, the photo shows heavy side damage. The fraud checks say why, coverage quotes the clause, payout shows every step." Click **Deny**, type a reason, submit. |
| 1:30–1:45 | Trace tab | "Every step, model call and tool call is in the audit trail, with timing, tokens and cost, and personal data is redacted. Coverage and fraud ran side by side." |
| 1:45–2:00 | Impact dashboard | "On a laptop with a local model this costs nothing per claim. Straight-through rate, escalation reasons and handling time are measured; hours saved uses a stated 45-minute baseline. Evals show 100% safe-escalation recall on held-out cases." |

## Checklist
- [ ] Run `docker compose run --rm api python -m claimpilot.seed` first (clean queue).
- [ ] Pre-submit the mismatched claim. With the claimant's token:
  ```bash
  curl -X POST localhost:8080/api/claims -H "Authorization: Bearer $TOKEN" --form-string 'form={"policy_number":"CP-100003","incident_date":"2026-05-10","incident_location":"Grocery store parking lot, Austin, TX","description":"Small scratch on the rear bumper from a shopping cart while parked. No other damage.","claimed_amount":6500}' -F photos=@data/photos/collision-side-impact.jpg
  ```
- [ ] Have `data/photos/collision-rear-corner-minor.jpg` ready to upload.
- [ ] Close other apps (Gemma needs about 8 GB of RAM).
- [ ] Keep the "Synthetic demo" banner visible in every shot.
