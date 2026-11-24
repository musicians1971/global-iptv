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
ADDITIONAL_RUSSIAN_SOURCES = {
    "Nexonic IPTV": "https://raw.githubusercontent.com/nexonic-technologies/nexonic-iptv/main/playlists/languages/ru.m3u",
    "IPTV Nexus": "https://dearbulut.github.io/iptv/playlists/language/rus.m3u",
    "NGRCH IPTV": "https://raw.githubusercontent.com/ngrch/iptv/main/ru.m3u",
    "NGRCH Music and Radio": "https://raw.githubusercontent.com/ngrch/iptv/main/music.m3u",
    "NGRCH Cartoons": "https://raw.githubusercontent.com/ngrch/iptv/main/cartoons.m3u",
    "IPTV Russia": "https://raw.githubusercontent.com/substanc1/iptv-russia/main/streams/ru.m3u",
    "DuckKota Russian playlist": "https://duckkota.github.io/iptv/playlists/language/rus.m3u",
}
USER_AGENT = "global-iptv-playlist-builder/1.0 (+https://github.com/musicians1971/global-iptv)"
TIMEOUT = float(os.environ.get("IPTV_TIMEOUT", "8"))
RETRY_TIMEOUT = float(os.environ.get("IPTV_RETRY_TIMEOUT", "12"))
AVAILABILITY_RETRIES = int(os.environ.get("IPTV_AVAILABILITY_RETRIES", "1"))
WORKERS = int(os.environ.get("IPTV_WORKERS", "48"))
RUSSIAN_LANGUAGE = "rus"  # ISO 639-3

CATEGORY_NAMES_RU = {
    "animation": "Мультфильмы", "auto": "Авто", "business": "Бизнес",
    "classic": "Классика", "comedy": "Комедии", "cooking": "Кулинария",
    "culture": "Культура", "documentary": "Документальные", "education": "Образование",
    "entertainment": "Развлечения", "family": "Семейные", "general": "Общие",
    "history": "История", "kids": "Детские", "legislative": "Государственные",
    "lifestyle": "Образ жизни", "movies": "Кино", "music": "Музыка",
    "news": "Новости", "outdoor": "На природе", "public": "Общественные",
    "quiz": "Викторины", "relax": "Отдых", "religious": "Религиозные",
    "series": "Сериалы", "science": "Наука", "shop": "Телемагазин",
    "sports": "Спорт", "travel": "Путешествия", "weather": "Погода",
    "undefined": "Без категории",
}
COUNTRY_NAMES_RU = {
    "RU": "Россия", "KZ": "Казахстан", "BY": "Беларусь", "UA": "Украина",
    "KG": "Кыргызстан", "UZ": "Узбекистан", "TJ": "Таджикистан", "MD": "Молдова",
    "AM": "Армения", "AZ": "Азербайджан", "GE": "Грузия", "EE": "Эстония",
    "LV": "Латвия", "LT": "Литва",
}


def fetch_additional_playlists() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Load extra Russian-language M3U inputs independently and tolerate outages."""
    records: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    for source_name, url in ADDITIONAL_RUSSIAN_SOURCES.items():
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read().decode("utf-8-sig")
            source_records = parse_m3u(text, source_name)
            records.extend(source_records)
            source_counts[source_name] = len(source_records)
        except Exception as exc:
            print(f"Additional Russian playlist unavailable ({source_name}): {type(exc).__name__}: {exc}", file=sys.stderr)
            source_counts[source_name] = 0
    return records, source_counts


def parse_m3u(text: str, source_name: str = "additional") -> list[dict[str, Any]]:
    """Parse EXTINF records and VLC request headers from an M3U playlist."""
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    options: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            attrs = {key.casefold(): value for key, value in re.findall(r'([\w-]+)="([^"]*)"', line)}
            title = line.split(",", 1)[1].strip() if "," in line else attrs.get("tvg-name", "")
            current = {
                "channel": attrs.get("tvg-id", ""), "title": title,
                "country": attrs.get("tvg-country", ""), "source": source_name,
                "group_title": attrs.get("group-title", ""),
                "language": attrs.get("tvg-language", ""),
                "referrer": attrs.get("http-referrer", ""),
                "user_agent": attrs.get("http-user-agent", ""),
            }
            options = {}
        elif line.startswith("#EXTVLCOPT:"):
            option = line.removeprefix("#EXTVLCOPT:")
            key, separator, value = option.partition("=")
            if separator:
                options[key.casefold()] = value
        elif line and not line.startswith("#") and current:
            if line.startswith(("http://", "https://")):
                current["url"] = line
                current["referrer"] = options.get("http-referrer", current["referrer"])
                current["user_agent"] = options.get("http-user-agent", current["user_agent"])
                records.append(current)
            current = None
            options = {}
    return records


def russian_sort_key(value: str) -> tuple[int, str]:
    normalized = value.casefold().replace("ё", "е")
    # Put Cyrillic names first; retain a deterministic order for other scripts.
    return (0 if re.search(r"[а-яё]", normalized) else 1, normalized)


def russian_channel_name(channel: dict[str, Any], fallback: str) -> str:
    for alternative in channel.get("alt_names", []) or []:
        if re.search(r"[а-яё]", str(alternative), re.IGNORECASE):
            return str(alternative)
    if re.search(r"[а-яё]", fallback, re.IGNORECASE):
        return fallback
    name = str(channel.get("name") or fallback)
    return name


def fetch_json(name: str) -> list[dict[str, Any]]:
    request = urllib.request.Request(f"{API}/{name}.json", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array from {name}.json")
    return data


def _probe_once(stream: dict[str, Any], timeout: float) -> tuple[bool, str, bool]:
    """Return (available, status, retryable) for a single request."""
    url = str(stream["url"])
    headers = {"User-Agent": stream.get("user_agent") or USER_AGENT}
    if stream.get("referrer"):
        headers["Referer"] = str(stream["referrer"])
    request = urllib.request.Request(
        url,
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type().casefold()
            body = response.read(4096)
            if response.status >= 400 or not body:
                return False, str(response.status), response.status in {408, 425, 429} or response.status >= 500
            if content_type in {"text/html", "application/json", "text/plain"}:
                return False, f"unexpected content type: {content_type}", False
            is_hls = ".m3u8" in response.geturl().casefold() or "mpegurl" in content_type
            if is_hls and not body.lstrip(b"\xef\xbb\xbf\t\r\n ").startswith(b"#EXTM3U"):
                return False, "invalid HLS playlist", False
            return True, str(response.status), False
    except urllib.error.HTTPError as exc:
        return False, str(exc.code), exc.code in {408, 425, 429} or exc.code >= 500
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, type(exc).__name__, True


def available(stream: dict[str, Any]) -> tuple[bool, str]:
    """Check a stream, retrying timeouts and temporary server errors once."""
    for attempt in range(AVAILABILITY_RETRIES + 1):
        timeout = RETRY_TIMEOUT if attempt else TIMEOUT
        ok, status, retryable = _probe_once(stream, timeout)
        if ok or not retryable or attempt >= AVAILABILITY_RETRIES:
            return ok, status
        time.sleep(0.25)
    return False, "unavailable"


def slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9а-яё]+", "-", value.lower()).strip("-")
    return result or "unknown"


def m3u(channels: list[dict[str, Any]]) -> str:
    lines = ["#EXTM3U"]
    for item in channels:
        name = str(item.get("name") or item.get("id") or "Unknown channel")
        attrs = [f'tvg-id="{item["id"]}"'] if item.get("id") else []
        if item.get("country"):
            attrs.append(f'tvg-country="{item["country"]}"')
        if item.get("referrer"):
            attrs.append(f'http-referrer="{_attribute(item["referrer"])}"')
        if item.get("user_agent"):
            attrs.append(f'http-user-agent="{_attribute(item["user_agent"])}"')
        cats = item.get("categories") or []
        group_title = item.get("display_category") or (cats[0] if cats else "")
        if group_title:
            attrs.append(f'group-title="{_attribute(group_title)}"')
        title = str(item.get("title") or name).replace("\r", " ").replace("\n", " ")
        lines.append(f"#EXTINF:-1 {' '.join(attrs)},{title}")
        lines.append(item["url"])
    return "\n".join(lines) + "\n"


def _attribute(value: str) -> str:
    return str(value).replace("&", "&amp;").replace('"', "&quot;").replace("\r", " ").replace("\n", " ")


def _normalized_channel_name(value: Any) -> str:
    """Normalize titles so mirror rows from community lists collapse together."""
    name = str(value or "").casefold().replace("ё", "е")
    name = re.sub(r"\s*\(\d+\)\s*$", "", name)
    name = re.sub(r"\s*\((?:\d{3,4}p|uhd|fhd|hd|sd)\)\s*$", "", name)
    name = re.sub(r"\b(?:\d{3,4}p|uhd|fhd|hd|sd)\b", " ", name)
    return " ".join(re.findall(r"[a-z0-9а-яё]+", name))


def write_grouped(directory: Path, groups: dict[str, list[dict[str, Any]]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("*.m3u"):
        old.unlink()
    for key, channels in sorted(groups.items()):
        (directory / f"{slug(key)}.m3u").write_text(m3u(channels), encoding="utf-8")


def build(
    streams: list[dict[str, Any]],
    channels: list[dict[str, Any]],
    countries: list[dict[str, Any]],
    feeds: list[dict[str, Any]],
    additional_streams: list[dict[str, Any]] | None = None,
    additional_source_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    channel_by_id = {c["id"]: c for c in channels if c.get("id")}
    channel_by_base_id = {
        str(c["id"]).split("@", 1)[0]: c for c in channels if c.get("id")
    }
    country_names = {c["code"].upper(): c["name"] for c in countries if c.get("code")}
    feed_by_key = {
        (feed.get("channel"), feed.get("id")): feed
        for feed in feeds
        if feed.get("channel") and feed.get("id")
    }
    russian_channels = {
        str(feed["channel"]).split("@", 1)[0]
        for feed in feeds
        if feed.get("channel")
        and RUSSIAN_LANGUAGE in {str(code).casefold() for code in feed.get("languages", [])}
    }
    channel_id_by_name: dict[str, str] = {}
    for channel in channels:
        if not channel.get("id"):
            continue
        for name in [channel.get("name"), *(channel.get("alt_names") or [])]:
            normalized = _normalized_channel_name(name)
            if normalized:
                channel_id_by_name.setdefault(normalized, str(channel["id"]).split("@", 1)[0])

    # URLs can be listed multiple times, including under different feeds. Keep one
    # record and prefer the first one that has a known channel ID.
    unique: dict[str, dict[str, Any]] = {}
    duplicates = 0
    excluded_labels = 0
    excluded_language = 0
    for stream in streams:
        url = stream.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        feed = feed_by_key.get((stream.get("channel"), stream.get("feed")))
        base_channel_id = str(stream.get("channel") or "").split("@", 1)[0]
        feed_is_russian = bool(
            feed and RUSSIAN_LANGUAGE in {str(code).casefold() for code in feed.get("languages", [])}
        )
        if not feed_is_russian and base_channel_id not in russian_channels:
            excluded_language += 1
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

    # The secondary feed is itself a Russian-language playlist. It adds candidates
    # only; the same availability check and URL deduplication apply to every entry.
    additional_candidates = 0
    for stream in additional_streams or []:
        url = stream.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        if url in unique:
            duplicates += 1
            continue
        unique[url] = stream
        additional_candidates += 1

    checks: dict[str, tuple[bool, str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(available, stream): url for url, stream in unique.items()}
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
        channel_id = str(stream.get("channel") or "")
        base_channel_id = channel_id.split("@", 1)[0] if channel_id else ""
        channel = channel_by_id.get(channel_id) or channel_by_base_id.get(base_channel_id)
        if channel and channel.get("is_nsfw"):
            continue
        fallback_name = stream.get("title") or stream.get("name")
        catalog_identity = channel_id_by_name.get(_normalized_channel_name(fallback_name))
        if catalog_identity:
            identity = catalog_identity
        elif base_channel_id in channel_by_base_id:
            identity = base_channel_id
        else:
            identity = _normalized_channel_name(fallback_name) or base_channel_id or url
        if identity not in selected:
            selected[identity] = (stream, channel or {})

    rows: list[dict[str, Any]] = []
    for stream, channel in selected.values():
        url = stream["url"]
        country_code = str(channel.get("country") or stream.get("country") or "").upper()
        fallback_name = stream.get("title") or stream.get("channel") or url
        display_name = russian_channel_name(channel, fallback_name)
        categories = channel.get("categories") or ([stream["group_title"]] if stream.get("group_title") else [])
        display_category = CATEGORY_NAMES_RU.get(str(categories[0]).casefold(), str(categories[0])) if categories else ""
        rows.append({
            "id": stream.get("channel") or "",
            "name": display_name,
            "title": display_name,
            "url": url,
            "country": country_code,
            "country_name": COUNTRY_NAMES_RU.get(country_code, country_names.get(country_code, "Неизвестная страна")),
            "categories": categories,
            "display_category": display_category,
            "referrer": stream.get("referrer") or "",
            "user_agent": stream.get("user_agent") or "",
        })
    rows.sort(key=lambda row: (russian_sort_key(row["country_name"]), russian_sort_key(row["name"]), row["url"]))

    (ROOT / "playlists").mkdir(exist_ok=True)
    (ROOT / "playlists" / "all.m3u").write_text(m3u(rows), encoding="utf-8")
    country_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    category_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["country"]:
            country_groups[f'{row["country"]}-{row["country_name"]}'].append(row)
        for category in row["categories"]:
            category_groups[CATEGORY_NAMES_RU.get(str(category).casefold(), str(category))].append(row)
    write_grouped(ROOT / "playlists" / "countries", country_groups)
    write_grouped(ROOT / "playlists" / "categories", category_groups)
    write_grouped(ROOT / "playlists" / "languages", {"rus": rows})

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    stats = {
        "generated_at": now.isoformat(),
        "source": f"{API}/streams.json",
        "source_streams": len(streams),
        "additional_sources": additional_source_counts or {},
        "additional_source_candidates": additional_candidates,
        "unique_candidate_urls": len(unique),
        "duplicate_urls_removed": duplicates,
        "excluded_labeled_streams": excluded_labels,
        "excluded_non_russian_or_unknown_language": excluded_language,
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
        streams, channels, countries, feeds = (fetch_json(name) for name in ("streams", "channels", "countries", "feeds"))
        additional_streams, source_counts = fetch_additional_playlists()
        stats = build(streams, channels, countries, feeds, additional_streams, source_counts)
    except Exception as exc:
        print(f"Playlist update failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({**stats, "elapsed_seconds": round(time.monotonic() - start, 1)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
