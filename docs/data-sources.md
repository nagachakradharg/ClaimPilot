# Data sources and licenses

All claimants, policies, claims and claim forms are **synthetic**. No real customer data is used (requirements §2 "Won't").

License review status as of 2026-11-24 (week 0).

| Data | Candidate | Finding | Decision |
|---|---|---|---|
| Damage photos | CarDD (USTC / CAS PIC Lab) | The [official license](https://cardd-ustc.github.io/docs/CarDD_license.pdf) is research-only. You need the lab's prior consent to get it, commercial use needs separate permission, and redistribution is restricted. The images themselves come from Flickr/Shutterstock under those sites' terms. | **Not used.** We can't re-host it in a public repo or demo. Use CC0/CC-BY car-damage photos (e.g. Wikimedia Commons, with attribution recorded per image) or authored photos instead. |
| Fraud labels / claim history seed | [Kaggle "Vehicle Claim Fraud Detection"](https://www.kaggle.com/datasets/shivamb/vehicle-claim-fraud-detection) (`fraud_oracle.csv`) | **CC0: Public Domain**, confirmed on the Kaggle page on 2026-11-24. | **Used.** Download to `data/raw/`. Derived, schema-mapped seeds may be committed; credit the source in the README. |
| Policy forms | Public personal auto policy forms (e.g. state DOI filings) | Reuse terms vary by publisher. | Author our own sample policy forms modeled on the standard structure (insuring agreement, exclusions, limits, deductibles), with clause ids. Cite reference sources in the README. |

## Rules
- Raw third-party data goes in `data/raw/` (git-ignored). Never commit it unless the license explicitly allows redistribution.
- Every committed image records its source URL, author and license in `data/photos/ATTRIBUTION.md`.
