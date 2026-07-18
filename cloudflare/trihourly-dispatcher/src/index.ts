interface Env {
  GITHUB_DISPATCH_TOKEN: string;
  SCHEDULE_STATE: KVNamespace;
  GITHUB_OWNER: string;
  GITHUB_REPO: string;
}

const SLOT_MS = 3 * 60 * 60 * 1000;
const RETENTION_SECONDS = 2 * 24 * 60 * 60;

function slotStartUtc(scheduledTime: number): string {
  return new Date(Math.floor(scheduledTime / SLOT_MS) * SLOT_MS).toISOString();
}

async function dispatch(env: Env, scheduledTime: number): Promise<void> {
  const slotUtc = slotStartUtc(scheduledTime);
  const key = `github-dispatch:${slotUtc}`;
  if (await env.SCHEDULE_STATE.get(key)) {
    console.log(JSON.stringify({ outcome: "already_dispatched", slot_utc: slotUtc }));
    return;
  }

  const dispatchedAtUtc = new Date().toISOString();
  const response = await fetch(
    `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/dispatches`,
    {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
        "X-GitHub-Api-Version": "2026-03-10",
        "Content-Type": "application/json",
        "User-Agent": "x-traders-cloudflare-dispatcher",
      },
      body: JSON.stringify({
        event_type: "cloudflare_trihourly_sync",
        client_payload: {
          slot_utc: slotUtc,
          dispatched_at_utc: dispatchedAtUtc,
          scheduler: "cloudflare-cron",
        },
      }),
    },
  );

  if (!response.ok) {
    const detail = (await response.text()).slice(0, 500);
    throw new Error(`GitHub repository_dispatch failed: HTTP ${response.status} ${detail}`);
  }

  // Only mark a slot after GitHub accepted it. A transient failure remains
  // eligible for a retry; a duplicate delivery is also serialized by GitHub.
  await env.SCHEDULE_STATE.put(key, JSON.stringify({ slot_utc: slotUtc, dispatched_at_utc: dispatchedAtUtc }), {
    expirationTtl: RETENTION_SECONDS,
  });
  console.log(JSON.stringify({ outcome: "dispatched", slot_utc: slotUtc, dispatched_at_utc: dispatchedAtUtc }));
}

export default {
  async scheduled(controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(dispatch(env, controller.scheduledTime));
  },
};
