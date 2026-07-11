#!/usr/bin/env python3
"""
fetch_tweets.py — multi-blogger tracker, P2 step 1

Pulls tweets for a single X user from twitterapi.io. Multi-blogger support:
each --user gets its own state/raw store under data/bloggers/{username}/, so
running this once per tracked influencer (see config/bloggers.json) never
cross-contaminates incremental watermarks — Twitter snowflake ids are globally
increasing but NOT safe to compare across different users' "newest seen" ids.

Design decisions (per project plan):
  - Endpoint: GET /twitter/user/last_tweets  (paginated, 20/page)
  - Auth: header  X-API-Key: <key>   (read from env var TWITTERAPI_KEY)
  - includeReplies = TRUE at the API, then we LOCALLY FILTER:
        keep  : main posts (not a reply) + self-threads (reply to the same user)
        drop  : replies to OTHER people (conversation noise)
    This is the only reliable way to preserve self-threads while dropping
    replies-to-others, because includeReplies=false may omit the thread tail.
  - Verbatim English text is preserved.
  - Incremental: remembers the newest tweet id seen per-user
    (data/bloggers/{user}/state.json) and stops early on the next run so you
    only pull new tweets (cheap).
  - Idempotent: de-dupes by tweet id when merging into
    data/bloggers/{user}/raw_tweets.json.

Cost: twitterapi.io is ~$0.15 / 1000 tweets.

Run:
    export TWITTERAPI_KEY="your_key_here"
    python fetch_tweets.py --user aleabitoreddit         # incremental (default)
    python fetch_tweets.py --user aleabitoreddit --backfill   # full history
    python fetch_tweets.py --user zephyr_z9              # another tracked blogger

To fetch all bloggers in config/bloggers.json in one go, use fetch_all.py.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ----------------------------------------------------------------------------- config
BASE_URL = "https://api.twitterapi.io"
DEFAULT_USER = "aleabitoreddit"

# files live under data/bloggers/{username}/ so each tracked influencer's
# incremental state and raw tweet store are fully isolated.
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"


def blogger_paths(username: str) -> tuple[Path, Path]:
    """Return (raw_tweets_path, state_path) for a given blogger username."""
    d = DATA_DIR / "bloggers" / username
    return d / "raw_tweets.json", d / "state.json"

PAGE_SLEEP_SEC = 0.4          # be polite between pages
MAX_PAGES_SAFETY = 2000       # hard stop so a bug can't loop forever (2000*20 = 40k tweets)
APPROX_COST_PER_TWEET = 0.15 / 1000  # USD, for a rough running estimate only


# ----------------------------------------------------------------------------- helpers
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')   # Windows console defaults to GBK, which can't print → etc.


def log(msg: str) -> None:
    print(msg, flush=True)


def get_api_key() -> str:
    key = os.environ.get("TWITTERAPI_KEY", "").strip()
    if not key:
        log("ERROR: environment variable TWITTERAPI_KEY is not set.")
        log('Set it first, e.g.  export TWITTERAPI_KEY="your_key_here"')
        sys.exit(1)
    return key


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"WARNING: {path.name} was corrupt; ignoring it.")
    return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic-ish: never leave a half-written file


def session_with_key(key: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"X-API-Key": key})
    return s


def get_user_id(s: requests.Session, username: str) -> str | None:
    """Resolve screen name -> numeric user id (more stable/faster for the timeline)."""
    try:
        r = s.get(f"{BASE_URL}/twitter/user/info",
                  params={"userName": username}, timeout=30)
        if r.status_code == 200:
            data = r.json()
            d = data.get("data") or data
            uid = d.get("id") or (d.get("user") or {}).get("id")
            if uid:
                return str(uid)
    except requests.RequestException as e:
        log(f"  (user id lookup failed, will fall back to userName: {e})")
    return None


def slim_tweet(t: dict, kind: str) -> dict:
    """Keep only the fields we use downstream; preserve verbatim text."""
    author = t.get("author") or {}
    return {
        "tweet_id": t.get("id"),
        "kind": kind,                               # post | self_thread | reply
        "url": t.get("url"),
        "created_at": t.get("createdAt"),
        "text": t.get("text"),                      # verbatim English, never altered
        "lang": t.get("lang"),
        "like_count": t.get("likeCount"),
        "retweet_count": t.get("retweetCount"),
        "reply_count": t.get("replyCount"),
        "quote_count": t.get("quoteCount"),
        "view_count": t.get("viewCount"),
        "bookmark_count": t.get("bookmarkCount"),
        "conversation_id": t.get("conversationId"),
        "is_reply": t.get("isReply"),
        "in_reply_to_id": t.get("inReplyToId"),
        "in_reply_to_username": t.get("inReplyToUsername"),
        "author_username": author.get("userName"),
        # keep nested refs so extract.py can tell original vs RT/quote
        "is_retweet": bool(t.get("retweeted_tweet")),
        "is_quote": bool(t.get("quoted_tweet")),
        "quoted_tweet_id": (t.get("quoted_tweet") or {}).get("id"),
        "retweeted_tweet_id": (t.get("retweeted_tweet") or {}).get("id"),
        "entities": t.get("entities"),              # hashtags/urls/mentions
        "cashtags": _extract_cashtags(t),           # structured $XXX list (extraction aid; non-$ forms handled by LLM later)
    }


def _extract_cashtags(t: dict) -> list:
    """Pull the API's auto-parsed $-symbols (entities.symbols[].text) as a hint.
    Non-cashtag forms like (4092), 'Sivers', .TW/.HK urls are left for the LLM."""
    out = []
    ents = t.get("entities") or {}
    for s in (ents.get("symbols") or []):
        if isinstance(s, dict) and s.get("text"):
            out.append(s["text"])
    return out


def reply_kind(t: dict, owner_username: str) -> str:
    """
    Classify each tweet so extract.py knows the context. We KEEP everything
    (decision 'B++'): a reply to someone else can still contain his deepest
    reasoning (e.g. answering a follower's question about $LPK / SIVE / 4092).
    Whether a tweet actually carries an investment view — and which ticker it
    refers to, including non-cashtag forms like (4092), 'Sivers', or 'it' — is a
    semantic judgement left to the LLM in extract.py, not to symbol matching here.

    Returns one of:
      'post'         : top-level post (not a reply)
      'self_thread'  : reply to himself (continues his own thread)
      'reply'        : reply to someone else (kept; LLM decides if it has substance)
    """
    if not t.get("isReply"):
        return "post"
    replied_to = (t.get("inReplyToUsername") or "").lower()
    if replied_to == owner_username.lower():
        return "self_thread"
    return "reply"


# ----------------------------------------------------------------------------- main fetch
def fetch(username: str, backfill: bool) -> None:
    key = get_api_key()
    s = session_with_key(key)

    raw_path, state_path = blogger_paths(username)
    state = load_json(state_path, {})
    stop_at_id = None if backfill else state.get("newest_tweet_id")
    mode = "BACKFILL (full history)" if backfill else "INCREMENTAL"
    log(f"Mode: {mode}")
    if stop_at_id:
        log(f"  will stop when reaching already-seen id {stop_at_id}")

    uid = get_user_id(s, username)
    base_params = {"includeReplies": "true"}         # pull everything, filter locally
    if uid:
        base_params["userId"] = uid
        log(f"Resolved @{username} -> userId {uid}")
    else:
        base_params["userName"] = username
        log(f"Using userName={username} (no userId resolved)")

    cursor = ""
    page = 0
    seen_total = 0          # tweets returned by API (for cost estimate)
    kept_new = []           # tweets we keep AND are new this run
    kind_counts = {}        # post / self_thread / reply tallies (this run)
    reached_known = False
    newest_id_this_run = None
    had_error = False       # set on network/HTTP/API errors so a bad handle or bad
                             # key doesn't silently look identical to "0 tweets, done"

    while True:
        page += 1
        if page > MAX_PAGES_SAFETY:
            log("Hit page safety limit; stopping.")
            break

        params = dict(base_params)
        if cursor:
            params["cursor"] = cursor

        try:
            r = s.get(f"{BASE_URL}/twitter/user/last_tweets", params=params, timeout=30)
        except requests.RequestException as e:
            log(f"  network error on page {page}: {e}; retrying once in 3s")
            time.sleep(3)
            try:
                r = s.get(f"{BASE_URL}/twitter/user/last_tweets", params=params, timeout=30)
            except requests.RequestException as e2:
                log(f"  retry failed: {e2}; stopping (partial data preserved).")
                had_error = True
                break

        if r.status_code != 200:
            log(f"  HTTP {r.status_code} on page {page}: {r.text[:200]}")
            had_error = True
            break

        payload = r.json()
        if payload.get("status") == "error":
            log(f"  API error: {payload.get('message') or payload.get('msg')}")
            had_error = True
            break

        # The API nests results under "data" (data.tweets), though the docs show
        # them at top level. Read from data first, fall back to top level.
        data = payload.get("data") or {}
        tweets = data.get("tweets") or payload.get("tweets") or []
        has_next = data.get("has_next_page")
        if has_next is None:
            has_next = payload.get("has_next_page")
        next_cur = data.get("next_cursor") or payload.get("next_cursor") or ""

        if not tweets:
            log(f"  page {page}: 0 tweets, done.")
            break

        seen_total += len(tweets)

        for t in tweets:
            tid = t.get("id")
            if newest_id_this_run is None:
                newest_id_this_run = tid             # first tweet of page 1 = newest overall
            # incremental stop: we've caught up to what we already have
            if stop_at_id and tid == stop_at_id:
                reached_known = True
                break
            kind = reply_kind(t, username)
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            kept_new.append(slim_tweet(t, kind))

        log(f"  page {page}: api={len(tweets)} kept_running={len(kept_new)} "
            f"cursor={'…' if cursor else '(start)'}")

        if reached_known:
            log("  reached already-seen tweet; stopping incremental pull.")
            break
        if not has_next:
            log("  no more pages.")
            break
        cursor = next_cur
        if not cursor:
            log("  empty next_cursor; stopping.")
            break
        time.sleep(PAGE_SLEEP_SEC)

    # ----- merge into raw store (de-dupe by tweet_id)
    existing = load_json(raw_path, [])
    by_id = {t["tweet_id"]: t for t in existing if t.get("tweet_id")}
    added = 0
    for t in kept_new:
        if t["tweet_id"] and t["tweet_id"] not in by_id:
            by_id[t["tweet_id"]] = t
            added += 1

    # sort newest first by created_at when possible, else by id
    def sort_key(t):
        return t.get("tweet_id") or ""
    merged = sorted(by_id.values(), key=sort_key, reverse=True)

    save_json(raw_path, merged)

    # update state
    if newest_id_this_run:
        state["newest_tweet_id"] = max(
            [newest_id_this_run] + ([state["newest_tweet_id"]] if state.get("newest_tweet_id") else []),
            key=lambda x: int(x) if str(x).isdigit() else 0,
        )
    state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
    state["username"] = username
    save_json(state_path, state)

    # ----- summary
    est_cost = seen_total * APPROX_COST_PER_TWEET
    log("")
    log("===== summary =====")
    log(f"  pages fetched      : {page}")
    log(f"  tweets seen (API)  : {seen_total}  (~${est_cost:.3f} est.)")
    log(f"  kept new this run  : {added}")
    log(f"  breakdown (run)    : posts={kind_counts.get('post',0)} "
        f"self_thread={kind_counts.get('self_thread',0)} reply={kind_counts.get('reply',0)}")
    log(f"  total in store     : {len(merged)}  -> {raw_path}")
    log(f"  newest id          : {state.get('newest_tweet_id')}")
    log("===================")

    if had_error and not merged:
        # A real failure (bad handle, bad key, API/network error) with ZERO usable
        # tweets — fail loudly here instead of writing an empty raw_tweets.json and
        # letting extract.py report a confusing unrelated-looking "no tweets" error
        # one step later. A legitimate "this account just has 0 tweets in range" is
        # NOT an error (had_error stays False in that case: see "0 tweets, done"
        # above, which is a clean break, not an error path).
        log(f"FAILING: no tweets were fetched for @{username} AND an error occurred "
            f"above — check the HTTP status/API error logged during this run "
            f"(401 = bad TWITTERAPI_KEY, 404 = handle not found, other = investigate).")
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Fetch @user tweets from twitterapi.io")
    ap.add_argument("--user", default=DEFAULT_USER, help="screen name (no @)")
    ap.add_argument("--backfill", action="store_true",
                    help="ignore saved state and pull full history")
    args = ap.parse_args()
    fetch(args.user, args.backfill)


if __name__ == "__main__":
    main()
