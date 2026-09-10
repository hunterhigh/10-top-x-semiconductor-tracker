#!/usr/bin/env python3
"""Merge provider-discovered profile avatars into the embedded cache.

Run explicitly during the refresh stage.  The dashboard builder reads the
resulting cache but never performs network I/O itself, keeping rendering
deterministic and offline-safe.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import html
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

try:
    from roster import load_bloggers
except ModuleNotFoundError:  # imported as scripts.refresh_avatars in tests
    from scripts.roster import load_bloggers


ROOT = Path(__file__).resolve().parent.parent
ALLOWED_AVATAR_HOSTS = frozenset({"pbs.twimg.com", "abs.twimg.com"})
MAX_AVATAR_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 20


class AvatarValidationError(ValueError):
    pass


def validate_avatar_url(url: object) -> str:
    if not isinstance(url, str) or not url:
        raise AvatarValidationError("profile data did not contain an avatar URL")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or host not in ALLOWED_AVATAR_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        raise AvatarValidationError(f"avatar URL is not allowlisted ({host or 'missing host'})")
    return url


class SafeAvatarRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_avatar_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


AVATAR_OPENER = build_opener(SafeAvatarRedirectHandler())


def detected_image_mime(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def clean_error(exc: Exception) -> str:
    return " ".join(f"{type(exc).__name__}: {exc}".split())[:200]


def fetch(url: str) -> tuple[str | None, str | None]:
    try:
        validate_avatar_url(url)
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with AVATAR_OPENER.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            validate_avatar_url(response.geturl())
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_AVATAR_BYTES:
                raise AvatarValidationError("avatar exceeds the 2 MiB limit")
            declared_mime = response.headers.get_content_type()
            raw = response.read(MAX_AVATAR_BYTES + 1)
        if len(raw) > MAX_AVATAR_BYTES:
            raise AvatarValidationError("avatar exceeds the 2 MiB limit")
        if not declared_mime.startswith("image/"):
            raise AvatarValidationError(f"avatar response was not an image ({declared_mime})")
        detected_mime = detected_image_mime(raw)
        if detected_mime is None:
            raise AvatarValidationError("avatar bytes have an unsupported image signature")
        return f"data:{detected_mime};base64," + base64.b64encode(raw).decode("ascii"), None
    except Exception as exc:
        return None, clean_error(exc)


def profile_avatar_url(profiles_root: Path, blogger_id: str, username: str) -> tuple[str | None, str | None]:
    path = profiles_root / blogger_id / "profile.json"
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
        if profile.get("schema_version") != 1:
            raise AvatarValidationError("unsupported profile schema")
        if not str(profile.get("id", "")).isdigit():
            raise AvatarValidationError("profile did not contain a numeric user id")
        if str(profile.get("user_name", "")).casefold() != username.casefold():
            raise AvatarValidationError("profile username does not match the configured account")
        if not isinstance(profile.get("observed_at"), str) or not profile["observed_at"]:
            raise AvatarValidationError("profile did not contain an observation time")
        return validate_avatar_url(profile.get("avatar_url")), None
    except Exception as exc:
        return None, clean_error(exc)


def valid_cached_avatar(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("data:image/") or ";base64," not in value:
        return False
    try:
        declared_mime = value[5:].split(";", 1)[0]
        raw = base64.b64decode(value.split(",", 1)[1], validate=True)
        if declared_mime == "image/svg+xml":
            return raw.lstrip().startswith(b"<svg") and b"<script" not in raw.lower()
        return detected_image_mime(raw) == declared_mime
    except (ValueError, binascii.Error):
        return False


def letter_avatar(letter: object, color: object) -> str:
    glyph = html.escape(str(letter or "?")[:2])
    fill = str(color or "#52616b")
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", fill):
        fill = "#52616b"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 160 160">'
        f'<rect width="160" height="160" rx="80" fill="{fill}"/>'
        f'<text x="80" y="102" text-anchor="middle" font-family="Arial,sans-serif" '
        f'font-size="72" font-weight="700" fill="#fff">{glyph}</text></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "bloggers.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "avatar_cache.json")
    parser.add_argument("--profiles-root", type=Path, default=ROOT / "data" / "bloggers")
    parser.add_argument("--status-output", type=Path)
    args = parser.parse_args()
    roster = load_bloggers(args.config)
    previous = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {}
    output = dict(previous)
    refreshed = []
    stale_cache = []
    fallback = []
    missing = []
    errors = {}
    for blogger in roster:
        blogger_id = blogger["id"]
        avatar_url, profile_error = profile_avatar_url(args.profiles_root, blogger_id, blogger["username"])
        image, error = fetch(avatar_url) if avatar_url else (None, profile_error)
        if image:
            output[blogger_id] = image
            refreshed.append(blogger_id)
        else:
            errors[blogger_id] = error
            if valid_cached_avatar(previous.get(blogger_id)):
                stale_cache.append(blogger_id)
            else:
                output[blogger_id] = letter_avatar(
                    blogger.get("avatar_letter") or blogger.get("display_name") or blogger_id,
                    blogger.get("color"),
                )
                fallback.append(blogger_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    cached = sum(valid_cached_avatar(output.get(blogger["id"])) for blogger in roster)
    summary = {
        "cached": cached,
        "refreshed": refreshed,
        "stale_cache": stale_cache,
        "fallback": fallback,
        "missing": missing,
        "errors": errors,
    }
    print(json.dumps(summary, ensure_ascii=False))
    if args.status_output:
        args.status_output.parent.mkdir(parents=True, exist_ok=True)
        args.status_output.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    if stale_cache:
        print(f"::warning::Avatar refresh failed for {len(stale_cache)} account(s); retained valid cached avatars.")
    if fallback:
        print(f"::warning::Using deterministic letter avatars for: {', '.join(fallback)}")
    if missing:
        print(f"::error::No valid current or cached avatar for: {', '.join(missing)}")
    return 0 if not missing else 2


if __name__ == "__main__": raise SystemExit(main())
