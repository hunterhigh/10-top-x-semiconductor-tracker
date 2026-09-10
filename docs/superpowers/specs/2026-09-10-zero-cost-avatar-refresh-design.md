# Zero-Cost Avatar Refresh Design

Date: 2026-09-10

Status: Approved for implementation

## Context

The trihourly pipeline already uses TwitterAPI.io for each tracked account. Before fetching tweets, `scripts/fetch_tweets.py` calls `GET /twitter/user/info` to resolve the account's numeric ID. That response also contains `profilePicture`, but the current implementation discards it.

Avatar refresh is implemented separately in `scripts/refresh_avatars.py`. It requests each public `x.com/{username}` page, parses the page's `og:image`, and downloads the image. X began returning HTTP 403 to all ten requests from GitHub-hosted runners on 2026-09-07. The same script had succeeded three hours earlier, so this is an external access-policy change rather than an account-specific or parsing failure.

The current cache-preservation fix keeps all ten dashboard avatars valid and prevents avatar failures from blocking core data publication. Health reporting correctly classifies the repeated refresh failure as `AVATAR_REFRESH_DEGRADED` / P2. This design replaces the unreliable discovery path without adding API calls, providers, credentials, or cost.

## Goals

- Stop requesting X profile pages from the production pipeline.
- Reuse the existing TwitterAPI.io user-info response and its `profilePicture` field.
- Add zero TwitterAPI.io requests compared with the current pipeline.
- Keep dashboard rendering deterministic and offline-safe by continuing to embed validated image data in `data/avatar_cache.json`.
- Preserve valid cached avatars during transient profile-image failures.
- Restore the sync health state to `healthy` when all ten current avatars refresh successfully.
- Keep account data publication fail-closed while treating avatar availability as a separately classified concern.

## Non-goals

- Adding the official X API, another avatar provider, a proxy, browser automation, cookies, or login sessions.
- Loading remote avatar URLs from the dashboard at render time.
- Storing complete TwitterAPI.io user responses.
- Changing tweet extraction, ticker mapping, pricing, dashboard layout, or the existing health thresholds.
- Automatically replacing a missing avatar with an unrelated generated or generic image.

## Chosen Architecture

The existing user-info lookup becomes the single source of both account identity and avatar discovery.

```text
TwitterAPI.io /twitter/user/info (existing request)
    -> validate id + userName + profilePicture
    -> persist one small data/bloggers/<id>/profile.json
    -> existing per-account workflow artifact
    -> publish job flattens all ten account artifacts
    -> refresh_avatars.py downloads allowlisted image URLs
    -> validate image bytes
    -> merge valid images into data/avatar_cache.json
    -> emit avatar-health.json for health classification
```

No step makes an additional TwitterAPI.io request. The publish job continues to receive no TwitterAPI.io credential.

## Components

### 1. User Profile Capture

`scripts/fetch_tweets.py` will replace the ID-only lookup contract with a profile lookup contract. The existing `GET /twitter/user/info?userName={handle}` request will return a normalized object containing:

- `id`
- `user_name`
- `avatar_url`
- `observed_at`

The tweet request continues to use `id` exactly as it does today. A successful profile response with an ID remains mandatory for account fetch success.

The normalized public profile fields will be written atomically to `data/bloggers/<blogger-id>/profile.json`. API keys, headers, full responses, biography text, follower metrics, and provider diagnostics will not be persisted.

### 2. Per-Account Profile File

Each profile file uses this schema:

```json
{
  "schema_version": 1,
  "id": "123456789",
  "user_name": "example",
  "avatar_url": "https://pbs.twimg.com/profile_images/...",
  "observed_at": "2026-09-10T03:20:00Z"
}
```

`id`, `user_name`, and `observed_at` are required. `avatar_url` is required for a fresh avatar result but its absence does not invalidate tweet data already returned by an otherwise valid account profile. When a successful profile response lacks `profilePicture`, the new profile file records `avatar_url: null`; the later avatar stage decides whether a valid old cache can be retained.

The file travels through the existing per-account artifact path, so parallel jobs never write the same file and no new artifact type is required.

### 3. Offline Avatar Merger

`scripts/refresh_avatars.py` will stop reading `x_url` and will never request `x.com`. It will read the ten profile files, validate their schema and identity, download each `avatar_url`, validate the response, and encode valid images as data URIs in `data/avatar_cache.json`.

The output cache remains keyed by the configured blogger ID. A failed candidate never overwrites a valid cached value.

The existing `--status-output` contract remains compatible. Its JSON continues to contain:

- `cached`
- `refreshed`
- `stale_cache`
- `missing`
- `errors`

This preserves compatibility with `scripts/write_sync_health.py` and the current workflow.

## Network and Content Validation

Avatar downloads must satisfy all of the following:

- HTTPS only.
- Initial and final redirect hosts must be in an explicit allowlist containing Twitter's profile-image CDN hosts used by the provider, initially `pbs.twimg.com` and `abs.twimg.com`.
- A finite timeout, with no unbounded retries.
- A fixed maximum response size of 2 MiB.
- `Content-Type` must begin with `image/`.
- Bytes must match a supported image signature: JPEG, PNG, GIF, or WebP.
- Empty bodies and HTML/error responses are rejected.

The downloader will use a bounded redirect implementation so a provider-supplied URL cannot redirect to an internal or arbitrary host. Logs may include the public host, account ID, HTTP status, and normalized error class, but never credentials or complete response bodies.

## Failure Semantics

### Account profile lookup

- Network, authentication, rate-limit exhaustion, provider error, invalid JSON, or missing numeric ID: preserve the current fail-closed account behavior and fail that matrix job.
- Missing `profilePicture` with a valid account ID: allow tweet synchronization to continue and persist `avatar_url: null`.

### Avatar candidate processing

- Valid fresh image: replace that account's cached avatar and add the account to `refreshed`.
- Missing/invalid profile file, missing URL, rejected host, timeout, HTTP error, invalid MIME, oversized body, or invalid image signature with a valid old cached avatar: retain the old value and add the account to `stale_cache` with a bounded error message.
- The same failure without a valid old cache: add the account to `missing` and return a non-zero exit code.

### Health mapping

- Ten valid fresh images: `healthy`, `severity=none`, `error_code=null`; consecutive degraded runs reset to zero.
- One or more stale-cache fallbacks and zero missing images: `degraded`, P2, `AVATAR_REFRESH_DEGRADED`.
- One or more missing images: `failed`, P1, `AVATAR_CACHE_MISSING`.

The core database and dashboard remain publishable during stale-cache fallback. They fail closed when any required account artifact is absent or when no valid current or cached avatar exists.

## Workflow Changes

The existing `fetch-extract` matrix and artifact upload remain unchanged structurally. The new `profile.json` is included automatically because the artifact already uploads the complete account directory.

The publish job keeps the current order:

1. Download and flatten all ten account artifacts.
2. Rebuild and validate core data.
3. Refresh prices.
4. Run the offline avatar merger using the ten profile files.
5. Validate enriched data and the dashboard.
6. Publish the complete snapshot.
7. Classify and write terminal health.

No TwitterAPI.io secret is added to the publish job, and no workflow step calls a new paid endpoint.

## Tests

### `tests/test_fetch_tweets.py`

- A single user-info response supplies both the numeric ID and `profilePicture`.
- Exactly one user-info request is made per account fetch, matching current behavior.
- A normalized profile file is written atomically after a valid response.
- Missing `profilePicture` writes `avatar_url: null` without concealing a valid ID.
- Invalid/missing ID retains the existing fail-closed behavior and does not advance watermarks.
- Persisted profile data contains no API key, headers, or full response body.

### `tests/test_refresh_avatars.py`

- Ten valid profile files update ten cache entries.
- No request targets `x.com` or any non-allowlisted host.
- Redirects to non-allowlisted hosts are rejected.
- Missing URL, HTTP 403, timeout, invalid MIME, oversized body, and invalid image signatures preserve valid cache and report bounded errors.
- Missing current and cached images return a non-zero exit code.
- Status JSON remains compatible with health classification.

### Integration and regression coverage

- Run the complete Python unit-test suite.
- Parse both workflow YAML files.
- Run the repository smoke workflow, including browser dashboard validation.
- Confirm the production matrix still publishes exactly ten account artifacts and profile files.
- Confirm request-count tests demonstrate zero new TwitterAPI.io calls.

## Rollout and Acceptance

Implementation will be developed on a dedicated branch and merged only after the smoke workflow succeeds.

After merge, trigger one full production sync and require:

- all ten account fetch jobs succeed;
- ten valid `profile.json` files reach the publish job;
- `refreshed` contains all ten blogger IDs;
- `stale_cache` and `missing` are empty;
- `valid_avatars` equals 10;
- the production workflow succeeds;
- `sync_health.json` becomes `healthy` with `severity=none` and `error_code=null`;
- `failure_state.consecutive_degraded_runs` resets to 0;
- no production log contains an attempted request to `x.com`.

If the provider supplies unusable image URLs, the rollout remains safe: the old cache stays intact, the health state remains P2, and the implementation branch can be reverted without rolling back synchronized tweet or market data.

## Rollback

Rollback consists of reverting the implementation commit. Existing `profile.json` files are harmless public metadata and may remain until a later cleanup, but the rollback commit should remove them if they were introduced only by this change. The pre-existing `data/avatar_cache.json` remains the recovery source throughout the rollout.
