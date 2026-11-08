# Naija Bites optimization — temporary artifact and cleanup register

**Created:** 2026-11-08. **Status:** ACTIVE / CLEANUP PENDING.
**Purpose:** Explicit inventory for cleanup **after validation and user confirmation**, not authorization to delete anything now.
**Safety:** Never delete an active deployment, production DB, production branch, or source data. For destructive cloud deletion, reconfirm the exact ID with the user and verify no remaining references. Neon project deletion is explicitly approval-gated.

## GitHub optimization stack (existing; do not change or delete during this experiment)

| PR | Temporary branch | Status |
|---|---|---|
| #42 | `optimise/phase-0-1` | Existing draft; preserve |
| #43 | `optimise/measurement-corrections-postgres-baseline-20261107` | Existing draft; preserve |
| #44 | `optimise/phase2-cart-inventory-batching-20261107` | Existing draft; preserve |
| #45 | `optimise/phase2-controlled-ab-postgres-20261107` | Existing draft; preserve |
| #46 | `optimise/phase2-pooling-experiment-20261108` | Existing draft; preserve |
| #47 | `optimise/phase2-pooling-safety-load-20261108` | Existing draft; preserve |
| #48 (planned) | `optimise/phase2-isolated-hosted-pool-validation-20261108` | New validation branch, stacked on #47; preserve until review |

These branches and their tests are **review artifacts**. After merging or explicitly abandoning the optimization stack, identify which PRs are closed, ensure no child PR depends on the branch, then ask before deleting branches. Retain important reproducible tests/documentation if merged; do not delete them solely because they are test files.

## Disposable hosted resources (new; NOT the existing owner staging)

| Resource | Unique identifier | Status | Deletion gate |
|---|---|---|---|
| Independent empty Neon PostgreSQL 18 project | `red-boat-54971627` | Created 2026-11-08, independent empty PG18 project | User reconfirms project ID; confirm not shared/production |
| Neon initial branch/database | `validation-empty` / `naija_pool_validation` in project `red-boat-54971627` | Created; independent project, not cloned from production | Deleted with dedicated project after approval |
| Dedicated Render validation web service | `srv-dbo3d43m8hqs73fesub0` (`naija-bites-pool-validation-temp-20261108`) | Created; Free/Virginia, auto-deploy off, isolated experiment branch | User reconfirms service ID; verify no other services depend on it |
| GitHub draft PR and new test-only scripts | `optimise/phase2-isolated-hosted-pool-validation-20261108`; `scripts/hosted_pool_validation.py` | Created; draft PR pending | Close/retain review history; delete branch only after approval |

**Existing resources explicitly excluded from cleanup:** Neon `naija-bites` project `fragrant-cake-44695541`, its `production` and `owner-mode-staging` branches, Render `naija-bites-demo-api`, Render `naija-bites-owner-staging`, and any existing customer data.

## Post-validation cleanup checklist

- [ ] Complete tests and save privacy-safe aggregate results, links, and failure findings.
- [ ] Confirm all test jobs are stopped; no Render deployment or other consumer points to the disposable DB.
- [ ] Revoke/rotate dedicated test credentials (if retained), never copy them to docs or PRs.
- [ ] Verify exact Render service ID and delete the **dedicated** service only after user approval.
- [ ] Verify exact Neon project ID and delete the **dedicated** project only after user approval.
- [ ] Confirm PR dependencies, then close/merge/abandon temporary branches in dependency order with approval.
- [ ] Remove any temporary test-only configuration/artifacts that are not needed for reproducibility, keeping valuable tests/docs.
- [ ] Recheck Render and Neon inventories for orphan resources and confirm final cleanup in this file.

**Retention:** No fixed deletion date has been specified. Do not promise unattended deletion; request confirmation when the user says testing is complete.
