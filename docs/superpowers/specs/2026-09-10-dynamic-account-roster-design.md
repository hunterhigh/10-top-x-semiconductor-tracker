# Dynamic Account Roster Design

Date: 2026-09-10

Status: Approved in conversation; awaiting written-spec review

## Context

The production repository currently models a fixed roster of ten X accounts. The same assumption is duplicated in `config/bloggers.json`, the `trihourly-sync` GitHub Actions matrix, the dashboard payload builder, the render-contract Schema, the dashboard validator, tests, manifest checks, packaged Skill copy, and user-facing descriptions of the seven-account opinion cohort.

The product is changing from “track ten fixed people” to “track selected traders and market-analysis accounts on X.” The active roster must therefore become the only source of account identity and cardinality. Adding or removing an account must not require a new API, a parallel payload, or a new renderer branch.

This change adds three opinion accounts and removes two accounts from the active roster:

- Add Frank trading (`@Franktradinglog`).
- Add AskLivermore (`@asklivermore`).
- Add Balder (`@Balder13946731`).
- Remove Zephyr (`zephyr_z9`) from active tracking.
- Remove Unusual Whales (`unusual_whales`) from active tracking.

The resulting active roster contains eleven accounts: nine `opinion`, one `news`, and one `disclosure` account. There is no active `flow` account after this migration.

## Goals

- Make `config/bloggers.json` the single source of active account identity, order, metadata, signal type, and cardinality.
- Add and backfill the three requested public accounts using stable internal blogger IDs.
- Preserve existing field names, field types, stock-document structure, payload structure, routes, and command entry points.
- Derive fetch jobs, profile coverage, account arrays, dashboard checks, top-pick checks, and manifest active-account counts from the roster.
- Preserve the historical source folders and database mentions for Zephyr and Unusual Whales while excluding them from new fetches and active-account consensus.
- Preserve original post text, reasons, display names, handles, company names, stock codes, dates, numbers, currencies, and source URLs byte-for-byte after provider decoding.
- Keep “not mentioned” distinct from “no direction / unclear” through storage, payload, and rendering.
- Regenerate and verify the manifest, index, stock documents, profile index, avatar cache, and dashboard artifact.

## Non-goals

- Building an account-management web service or a second account API.
- Changing dashboard layout or stock-analysis methodology.
- Reclassifying historical mentions for existing accounts.
- Deleting Zephyr or Unusual Whales source data or historical evidence.
- Translating or editorially rewriting post evidence or extracted reasons.
- Treating news, disclosure, or future non-opinion accounts as opinion consensus votes.

## Stable Identity

New accounts use internal IDs that are independent of mutable X handles:

| `blogger_id` | Display name | Current handle | X URL | Signal type |
| --- | --- | --- | --- | --- |
| `frank_trading` | Frank trading | `@Franktradinglog` | `https://x.com/Franktradinglog` | `opinion` |
| `asklivermore` | AskLivermore | `@asklivermore` | `https://x.com/asklivermore` | `opinion` |
| `balder` | Balder | `@Balder13946731` | `https://x.com/Balder13946731` | `opinion` |

The provider’s immutable numeric X user ID is stored in each account’s `profile.json` and checked on subsequent refreshes. `blogger_id` remains the database and folder identity; `handle` remains the network lookup name. A handle change updates roster metadata without renaming the source folder or historical records.

Existing blogger IDs are not renamed because doing so would break historical references.

## Resulting Active Roster

The active roster, in display order, is:

1. `aleabitoreddit` — Serenity — opinion
2. `jukan05` — Jukan — opinion
3. `KawzInvests` — KawzInvests — opinion
4. `michaelsikand` — Michael Sikand — opinion
5. `ren_stocks` — Ren — opinion
6. `octopusycc` — 大老师 — opinion
7. `frank_trading` — Frank trading — opinion
8. `asklivermore` — AskLivermore — opinion
9. `balder` — Balder — opinion
10. `StockMKTNewz` — Evan — news
11. `DJTRadar` — DJT Radar — disclosure

Roster order remains presentation order. Consensus membership is derived only from `signal_type == "opinion"`, not from position or a fixed slice.

## Chosen Architecture

`config/bloggers.json` becomes the production roster contract.

```text
config/bloggers.json
    -> validate unique blogger_id, handle, URL, and signal_type
    -> resolve-base emits a JSON GitHub Actions matrix
    -> one fetch/extract artifact per active blogger_id
    -> publish requires the artifact identity set to equal the roster identity set
    -> build_db preserves the full historical corpus
    -> payload filters active people and consensus membership from the roster
    -> Schema and invariant checks validate roster equality, not a literal count
    -> renderer consumes the unchanged payload interface
```

The workflow fails closed for an empty roster, duplicate identities, invalid signal types, a missing active-account artifact, or an unexpected active-account artifact. A valid account-count change is not itself an error.

## Components

### 1. Roster Loader and Validation

A shared deterministic roster loader will read `config/bloggers.json` and validate:

- The top-level `bloggers` value is a non-empty array.
- Every entry retains `id`, `display_name`, `handle`, `x_url`, and `signal_type` with their existing types.
- `id`, case-insensitive handle, and canonical X URL are unique.
- `signal_type` remains one of `opinion`, `flow`, `news`, or `disclosure`.
- `handle` and `x_url` identify the same X username.
- No code infers consensus membership from roster length or array position.

Existing entry points may keep their current arguments. Fetching gains an internal distinction between `blogger_id` and X username, with backwards-compatible defaults for existing callers whose ID equals their username.

### 2. Dynamic GitHub Actions Matrix

`resolve-base` will serialize the validated active roster into a compact JSON matrix and publish the active count and ID set as job outputs. `fetch-extract` will consume that matrix with `fromJSON` and name artifacts by stable `blogger_id`.

The publish job will:

1. Download all active account artifacts.
2. Compare artifact IDs with the roster ID set.
3. Reject missing, duplicate, or unexpected active-account artifacts.
4. Continue through the existing database, prices, avatars, payload, renderer, validation, commit, artifact, and health sequence.

The scheduler, workflow name, external dispatch contract, and production interface remain unchanged.

### 3. New Account Capture

Each new account receives:

```text
data/bloggers/<blogger_id>/profile.json
data/bloggers/<blogger_id>/raw_tweets.json
data/bloggers/<blogger_id>/extracted.json
data/bloggers/<blogger_id>/state.json
```

The production provider lookup supplies the numeric X user ID, current username, public profile-image URL, and public posts. The initial scope is the existing `recent_30d` policy. The normal extractor writes the existing mention schema, including `created_at`, ET `date`, `tweet_id`, `blogger_id`, `mention_type`, `stance`, `reasons`, `text`, `url`, and instrument fields.

Provider or extraction failure must not create an empty success state or advance a watermark. A complete backfill is required before the new roster can publish.

### 4. Profiles and Avatars

The three new editorial profile entries will be added to `config/blogger_profiles.json` without modifying existing keys or value types. Removed-account editorial entries remain available as historical metadata. Active payload generation filters profiles by the active roster.

`data/db/blogger_profiles.json` will be rebuilt with the existing structure. Active profile-coverage counts are calculated against the eleven active IDs, while preserved historical profiles may remain queryable.

`scripts/refresh_avatars.py` will iterate the active roster. It first uses the validated public CDN URL captured in `profile.json`; a valid previous cache is retained on transient failure. If neither a real image nor valid cached image is available for a newly added account, a deterministic letter avatar is used only for that account and recorded in validation output. Removed accounts may remain in `data/avatar_cache.json` as historical cache entries but are not counted as active coverage.

### 5. Historical Preservation

The source folders for `zephyr_z9` and `unusual_whales` remain unchanged. Database rebuilds continue to retain their existing stock mentions so historical reports and account-attributed evidence do not disappear.

Active roster membership controls new fetches, active people cards, current consensus, top-pick cards, and per-stock active-account state arrays. Historical mentions from inactive accounts remain source-linked records but do not enter new active-account consensus calculations.

`manifest.tracked_bloggers` represents the active roster count. `mentions_by_blogger` remains a corpus accounting map and may include inactive historical blogger IDs; consumers must not infer active membership from that map.

### 6. Direction Semantics

The persisted mention field keeps the existing Schema enum:

- `bullish`
- `bearish`
- `neutral`

The unchanged stock-person state contract continues to distinguish:

- `bull_only` -> bullish
- `bear_only` -> bearish
- `both` or `no_direction` -> no direction / unclear, depending on the existing renderer context
- `not_mentioned` -> not mentioned

Only an explicit record can produce `no_direction`; absence of a record produces `not_mentioned`. Missing data is never normalized to `neutral`, and non-opinion accounts never enter opinion consensus arithmetic.

### 7. Dynamic Payload and Schema Validation

The hard-coded fallback roster in `dashboard_payload.py` will be removed. The payload builder will load roster metadata from the established config/profile inputs and use the active ID set for people, opinion membership, top picks, and stock drilldowns.

The JSON Schema will remove `minItems: 10` and `maxItems: 10` from roster-sized arrays. Cross-field invariants will enforce instead:

- `people` IDs equal the active roster IDs.
- Each stock drilldown’s `person_windows` and `people_by_window` IDs equal the active roster IDs.
- Monthly top-pick IDs equal the active roster IDs, with one entry per active account.
- IDs are unique in every roster-sized collection.
- The opinion cohort equals active people whose `signal_type` is `opinion`.

Business thresholds such as “at least three directional accounts” and result-list limits such as “top ten reasons” are not roster-size assumptions and remain unchanged.

### 8. Manifest and Hashes

The normal database and price pipeline will regenerate `data/db/manifest.json`. The existing manifest interface remains intact. At minimum the following values will be recomputed and verified:

- `generated_at`
- `tracked_bloggers`
- `mentions_by_blogger`
- `total_mentions`
- `date_range`
- `profile_coverage`
- `index_sha256`
- `price_cache_index_sha256`
- stock and price-cache counts and storage statistics

`scripts/verify_data.py` will hash the serialized index and related files using the existing canonical method and fail on any mismatch. No manifest value will be edited by hand after generation.

## Error Handling

- Invalid or duplicate roster entry: fail before any account fetch.
- Provider authentication, rate-limit, HTTP, identity, or JSON failure: fail that account and block publication.
- Numeric X user ID changes for an existing stable blogger ID: fail identity validation and require review.
- Partial extraction or missing state file: block database publication.
- Real avatar unavailable with valid cache: retain cache and report degraded health under the existing policy.
- Real avatar and cache unavailable for a new account: generate the deterministic letter avatar, report the fallback explicitly, and keep the payload structurally valid.
- Payload/roster set mismatch, hash mismatch, Schema failure, or browser-render failure: do not commit the snapshot or upload the dashboard artifact.

## Testing

### Unit coverage

- Roster validation accepts 8-, 11-, and 12-account fixtures.
- Empty roster, duplicate ID, duplicate handle, invalid URL, and invalid signal type fail.
- Stable blogger ID and current handle are passed separately to fetch and storage.
- Removing an active account does not delete its source folder or historical stock mentions.
- `not_mentioned` remains distinct from explicit neutral/no-direction evidence.
- Only active `opinion` accounts participate in consensus.
- Manifest active count and corpus mention map may differ without losing integrity.
- Avatar coverage uses active IDs and supports real, cached, and deterministic fallback images.

### Integration coverage

- Parse all workflow YAML.
- Run the complete Python test suite.
- Run `scripts/build_db.py` and `scripts/verify_data.py`.
- Run `skill/scripts/dashboard_payload.py` against the regenerated database.
- Run `skill/scripts/analyze_stock.py` on stock documents containing new-account records and mixed active/historical authors.
- Run `skill/scripts/render_dashboard.py` with no account-specific branch.
- Run `skill/scripts/validate_dashboard.py --browser required`, deriving the expected account set from the payload/roster instead of a literal number.
- Verify the packaged Skill resources match their canonical repository counterparts.

## Rollout and Acceptance

Implementation will use the dedicated `agent/dynamic-roster` branch. Before merge, the smoke workflow must pass with the dynamic eleven-account roster.

After merge, one manually triggered full production sync must demonstrate:

- eleven account fetch/extract jobs succeed;
- the three new folders contain complete profile, raw, extracted, and state files;
- the three numeric X user IDs are unique and stable across repeated lookup;
- Zephyr and Unusual Whales receive no new fetch job;
- all active IDs appear exactly once in roster-sized payload collections;
- the opinion cohort contains the nine active opinion accounts;
- `dashboard_payload.py`, `analyze_stock.py`, and the renderer run without account-specific branches;
- manifest and index hashes verify;
- the dashboard artifact passes structural and browser validation;
- terminal sync health is healthy or, only for a retained valid avatar cache, explicitly degraded under the existing P2 policy.

The final handoff will list the full active roster, confirmed new blogger IDs, created file paths, all removed cardinality assumptions, manifest version and hash results, and one unchanged source-linked example record from a new account.

## Rollback

Rollback reverts the implementation commit and restores the prior active roster. The new public source folders may remain as unreferenced recoverable data or be removed in the revert commit if they were never published. Zephyr and Unusual Whales history remains intact throughout. Because the public payload and API structure do not change, rollback does not require a consumer migration.
