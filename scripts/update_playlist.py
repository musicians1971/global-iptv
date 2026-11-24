#!/usr/bin/env python3
"""Build deduplicated M3U playlists from iptv-org's public JSON API."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API = "https://iptv-org.github.io/api"
USER_AGENT = "global-iptv-playlist-builder/1.0 (+https://github.com/musicians1971/global-iptv)"
TIMEOUT = float(os.environ.get("IPTV_TIMEOUT", "8"))
WORKERS = int(os.environ.get("IPTV_WORKERS", "48"))


def fetch_json(name: str) -> list[dict[str, Any]]:
    request = urllib.request.Request(f"{API}/{name}.json", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array from {name}.json")
    return data


def available(url: str) -> tuple[bool, str]:
    """Probe a URL with a short GET; HEAD is unreliable for streaming hosts."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            response.read(1)
            return response.status < 400, str(response.status)
    except urllib.error.HTTPError as exc:
        # A range request may be rejected even when the endpoint is alive.
        if exc.code in (400, 405, 416, 501):
            return True, f"{exc.code} (range unsupported)"
        return False, str(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, type(exc).__name__


def slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return result or "unknown"


def m3u(channels: list[dict[str, Any]]) -> str:
    lines = ["#EXTM3U"]
    for item in channels:
        name = str(item.get("name") or item.get("id") or "Unknown channel")
        attrs = [f'tvg-id="{item["id"]}"'] if item.get("id") else []
        if item.get("country"):
            attrs.append(f'tvg-country="{item["country"]}"')
        cats = item.get("categories") or []
        if cats:
            attrs.append(f'group-title="{cats[0]}"')
        title = str(item.get("title") or name).replace("\r", " ").replace("\n", " ")
        lines.append(f"#EXTINF:-1 {' '.join(attrs)},{title}")
        lines.append(item["url"])
    return "\n".join(lines) + "\n"


def write_grouped(directory: Path, groups: dict[str, list[dict[str, Any]]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("*.m3u"):
        old.unlink()
    for key, channels in sorted(groups.items()):
        (directory / f"{slug(key)}.m3u").write_text(m3u(channels), encoding="utf-8")


def build(streams: list[dict[str, Any]], channels: list[dict[str, Any]], countries: list[dict[str, Any]]) -> dict[str, Any]:
    channel_by_id = {c["id"]: c for c in channels if c.get("id")}
    country_names = {c["code"].upper(): c["name"] for c in countries if c.get("code")}

    # URLs can be listed multiple times, including under different feeds. Keep one
    # record and prefer the first one that has a known channel ID.
    unique: dict[str, dict[str, Any]] = {}
    duplicates = 0
    excluded_labels = 0
    for stream in streams:
        url = stream.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        labels = {str(label).casefold() for label in stream.get("labels", [])}
        if labels.intersection({"geo-blocked", "not 24/7"}):
            excluded_labels += 1
            continue
        old = unique.get(url)
        if old:
            duplicates += 1
            if not old.get("channel") and stream.get("channel"):
                unique[url] = stream
        else:
            unique[url] = stream

    checks: dict[str, tuple[bool, str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(available, url): url for url in unique}
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            try:
                checks[url] = future.result()
            except Exception as exc:  # Keep a failed probe from aborting the run.
                checks[url] = (False, type(exc).__name__)

    # Prevent multiple URLs for one channel from cluttering the consumer playlist.
    selected: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for url, stream in unique.items():
        ok, _ = checks[url]
        if not ok:
            continue
        channel = channel_by_id.get(stream.get("channel"))
        if channel and channel.get("is_nsfw"):
            continue
        identity = str(stream.get("channel") or url)
        if identity not in selected:
            selected[identity] = (stream, channel or {})

    rows: list[dict[str, Any]] = []
    for stream, channel in selected.values():
        url = stream["url"]
        country_code = str(channel.get("country") or "").upper()
        rows.append({
            "id": stream.get("channel") or "",
            "name": channel.get("name") or stream.get("title") or stream.get("channel") or url,
            "title": stream.get("title") or channel.get("name") or stream.get("channel") or url,
            "url": url,
            "country": country_code,
            "country_name": country_names.get(country_code, "Unknown"),
            "categories": channel.get("categories") or [],
        })
    rows.sort(key=lambda row: (row["country_name"].casefold(), row["name"].casefold(), row["url"]))

    (ROOT / "playlists").mkdir(exist_ok=True)
    (ROOT / "playlists" / "all.m3u").write_text(m3u(rows), encoding="utf-8")
    country_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    category_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["country"]:
            country_groups[f'{row["country"]}-{row["country_name"]}'].append(row)
        for category in row["categories"]:
            category_groups[str(category)].append(row)
    write_grouped(ROOT / "playlists" / "countries", country_groups)
    write_grouped(ROOT / "playlists" / "categories", category_groups)

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    stats = {
        "generated_at": now.isoformat(),
        "source": f"{API}/streams.json",
        "source_streams": len(streams),
        "unique_candidate_urls": len(unique),
        "duplicate_urls_removed": duplicates,
        "excluded_labeled_streams": excluded_labels,
        "checked_urls": len(checks),
        "available_urls": sum(1 for ok, _ in checks.values() if ok),
        "available_channels": len(rows),
        "countries": len(country_groups),
        "categories": len(category_groups),
        "probe_timeout_seconds": TIMEOUT,
        "availability_note": "Availability reflects this run's network and may differ by viewer location.",
    }
    (ROOT / "stats.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    return stats


def main() -> int:
    start = time.monotonic()
    try:
        streams, channels, countries = (fetch_json(name) for name in ("streams", "channels", "countries"))
        stats = build(streams, channels, countries)
    except Exception as exc:
        print(f"Playlist update failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({**stats, "elapsed_seconds": round(time.monotonic() - start, 1)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
