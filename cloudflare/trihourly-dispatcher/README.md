# Cloudflare trihourly dispatcher

This Worker is the only automatic scheduler. It dispatches the GitHub
`trihourly-sync` workflow at `5 */3 * * *` UTC and records one key per UTC
three-hour slot in Cloudflare KV. GitHub remains the only system that fetches,
extracts, builds, validates, commits, and publishes tracker data.

## One-time deployment

1. Create a Cloudflare KV namespace named `x-traders-schedule-state`.
2. Replace `REPLACE_WITH_CLOUDFLARE_KV_NAMESPACE_ID` in `wrangler.jsonc` with
   its production namespace ID. Do not put secrets in that file.
3. Create a GitHub fine-grained token restricted to
   `hunterhigh/10-top-x-semiconductor-tracker`, with the minimum permission
   required to create a repository dispatch (Contents write). Prefer a GitHub
   App installation token if one is already available.
4. From this directory, authenticate Wrangler and run:

   ```bash
   npx wrangler secret put GITHUB_DISPATCH_TOKEN
   npx wrangler deploy
   ```

5. In Cloudflare, confirm the Cron Trigger is `5 */3 * * *` UTC. Trigger a
   one-off scheduled test from the Worker dashboard, then verify a
   `repository_dispatch`-triggered run appears in GitHub Actions. Its Summary
   must report the matching Cloudflare slot and `10/10` successful accounts.

## Operational rules

- Do not restore a GitHub `schedule` trigger while this Worker is deployed.
- Do not store `TWITTERAPI_KEY`, model keys, or market-data keys in Cloudflare.
- A GitHub API failure leaves the slot unmarked so the platform may retry.
  A duplicate accepted dispatch is harmless to data integrity because the
  GitHub workflow's shared concurrency group serializes writers; Cloudflare KV
  prevents the normal duplicate path.
- Cron configuration changes can take several minutes to propagate. Do not
  judge the first execution until the new trigger is visible in Cloudflare.
