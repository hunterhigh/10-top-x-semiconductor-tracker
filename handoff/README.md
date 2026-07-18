# Dashboard handoff index

## Current source of truth

`10V-dashboard-backend-handoff-final-2026-07-17/` is the current, product-approved dashboard handoff. It supersedes the earlier frontend-only handoff dated 2026-07-14 for UI implementation and backend integration decisions.

Start with:

1. `README-START-HERE.md` for the implementation order and non-negotiable boundaries.
2. `01-final-ui/10-market-voices-complete.html` for the sole approved UI reference.
3. `02-backend-contract/BACKEND-HANDOFF.md` and `dashboard-render-contract.schema.json` for renderer work.
4. `03-rules-and-tests/` for deterministic aggregation rules and their tests.

## Sync record

- Synced: 2026-07-18
- Source archive: `10V-dashboard-backend-handoff-final-2026-07-17.rar`
- Archive SHA-256: `35173619072EEC5222E80FEBFD8386CE20DB20266466CD6450559DA46D75CAF1`
- Extraction: 12 handoff files, preserved in their original folder structure.
- Scope: final single-file UI, renderer payload contract, aggregation rules/tests, blogger roles, backend field map, validation record, and change history.

## Working rules

- Do not treat the HTML's demo data as a production data source.
- The production renderer must remain deterministic and produce one self-contained HTML file.
- Reuse the bundled aggregation rules; do not infer direction from post text during rendering.
- Only the seven `opinion` accounts participate in opinion consensus. `flow`, `news`, and `disclosure` remain standalone signals.
- Record future confirmed UI, data-contract, or aggregation changes in `10V-dashboard-backend-handoff-final-2026-07-17/04-reference/CHANGE-HANDOFF-FULL.md`; do not overwrite this snapshot.

## Historical material

The workspace-level `handoff/x-traders-consensus-frontend-handoff-2026-07-14/` remains historical reference only. When it conflicts with this 2026-07-17 package, use the 2026-07-17 package.
