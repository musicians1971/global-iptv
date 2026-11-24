import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.update_playlist as updater


class PlaylistTests(unittest.TestCase):
    def test_slug_is_safe_and_stable(self):
        self.assertEqual(updater.slug("United States / News"), "united-states-news")
        self.assertEqual(updater.slug("Россия / Новости"), "россия-новости")
        self.assertEqual(updater.slug("!!!"), "unknown")

    def test_parse_m3u_reads_ids_urls_and_vlc_headers(self):
        playlist = (
            '#EXTM3U\n#EXTINF:-1 tvg-id="Example.ru" tvg-country="RU",Новости\n'
            "#EXTVLCOPT:http-referrer=https://example.test/\n"
            "https://example.test/live.m3u8\n"
        )
        records = updater.parse_m3u(playlist, "Test source")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["channel"], "Example.ru")
        self.assertEqual(records[0]["title"], "Новости")
        self.assertEqual(records[0]["referrer"], "https://example.test/")
        self.assertEqual(records[0]["source"], "Test source")

    def test_russian_names_sort_before_latin_names(self):
        self.assertLess(updater.russian_sort_key("Новости"), updater.russian_sort_key("News"))
        self.assertEqual(updater.russian_sort_key("Ёлка"), updater.russian_sort_key("Елка"))

    def test_m3u_has_metadata_headers_and_escaped_single_line_title(self):
        content = updater.m3u([{
            "id": "Example.us", "name": "Example", "title": "Example\nHD",
            "url": "https://example.test/live.m3u8", "country": "US",
            "categories": ["news"], "referrer": "https://site.test/?x=\"y\"",
            "user_agent": "Player/1",
        }])
        self.assertIn('tvg-id="Example.us" tvg-country="US" http-referrer="https://site.test/?x=&quot;y&quot;" http-user-agent="Player/1" group-title="news",Example HD', content)
        self.assertEqual(content.count("https://example.test/live.m3u8"), 1)

    def test_build_filters_to_russian_feeds_and_groups_only_available_streams(self):
        streams = [
            {"channel": "One.us", "feed": "Russian", "url": "https://one.test/a.m3u8"},
            {"channel": "One.us", "feed": "Russian", "url": "https://one.test/a.m3u8"},
            {"channel": "Two.ca", "feed": "English", "url": "https://two.test/a.m3u8"},
            {"channel": "Three.us", "feed": "Russian", "url": "https://three.test/a.m3u8", "labels": ["Geo-blocked"]},
        ]
        channels = [
            {"id": "One.us", "name": "One", "country": "US", "categories": ["news"]},
            {"id": "Two.ca", "name": "Two", "country": "CA", "categories": ["sports"]},
            {"id": "Three.us", "name": "Three", "country": "US", "categories": ["news"]},
        ]
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(updater, "ROOT", Path(temp)), patch.object(
                updater, "available", side_effect=lambda stream: ("two.test" not in stream["url"], "200")
            ):
                stats = updater.build(
                    streams,
                    channels,
                    [{"code": "US", "name": "United States"}, {"code": "CA", "name": "Canada"}],
                    [
                        {"channel": "One.us", "id": "Russian", "languages": ["rus"]},
                        {"channel": "Two.ca", "id": "English", "languages": ["eng"]},
                        {"channel": "Three.us", "id": "Russian", "languages": ["rus"]},
                    ],
                )
            root = Path(temp)
            all_playlist = (root / "playlists" / "all.m3u").read_text(encoding="utf-8")
            self.assertIn("One.us", all_playlist)
            self.assertNotIn("Two.ca", all_playlist)
            self.assertNotIn("Three.us", all_playlist)
            self.assertTrue((root / "playlists" / "countries" / "us-united-states.m3u").exists())
            self.assertTrue((root / "playlists" / "languages" / "rus.m3u").exists())
            saved_stats = json.loads((root / "stats.json").read_text(encoding="utf-8"))
            self.assertEqual(stats["duplicate_urls_removed"], 1)
            self.assertEqual(stats["available_channels"], 1)
            self.assertEqual(stats["excluded_non_russian_or_unknown_language"], 1)
            self.assertEqual(saved_stats["excluded_labeled_streams"], 1)

    def test_additional_playlist_stream_is_checked_and_localized(self):
        streams = []
        channels = [{
            "id": "Example.kz", "name": "Example", "alt_names": ["Пример"],
            "country": "KZ", "categories": ["news"],
        }]
        additional = [{
            "channel": "Example.kz@HD", "title": "Example HD", "country": "KZ",
            "url": "https://example.test/live.m3u8", "source": "External list",
        }]
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(updater, "ROOT", Path(temp)), patch.object(
                updater, "available", return_value=(True, "200")
            ):
                stats = updater.build(streams, channels, [{"code": "KZ", "name": "Kazakhstan"}], [], additional)
            playlist = (Path(temp) / "playlists" / "all.m3u").read_text(encoding="utf-8")
            self.assertIn("group-title=\"Новости\",Пример", playlist)
            self.assertEqual(stats["additional_source_candidates"], 1)
            self.assertEqual(stats["available_channels"], 1)

    def test_available_rejects_error_pages_and_invalid_hls(self):
        class FakeHeaders:
            def get_content_type(self):
                return "application/vnd.apple.mpegurl"

        class FakeResponse:
            status = 200
            headers = FakeHeaders()

            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _size):
                return self.body

            def geturl(self):
                return "https://sample.test/live.m3u8"

        with patch.object(updater.urllib.request, "urlopen", return_value=FakeResponse(b"<html>blocked</html>")):
            self.assertFalse(updater.available({"url": "https://sample.test/live.m3u8"})[0])
        with patch.object(updater.urllib.request, "urlopen", return_value=FakeResponse(b"#EXTM3U\n#EXT-X-VERSION:3\n")):
            self.assertTrue(updater.available({"url": "https://sample.test/live.m3u8"})[0])


if __name__ == "__main__":
    unittest.main()
