import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.update_playlist as updater


class PlaylistTests(unittest.TestCase):
    def test_slug_is_safe_and_stable(self):
        self.assertEqual(updater.slug("United States / News"), "united-states-news")
        self.assertEqual(updater.slug("!!!"), "unknown")

    def test_m3u_has_metadata_and_escaped_single_line_title(self):
        content = updater.m3u([{
            "id": "Example.us", "name": "Example", "title": "Example\nHD",
            "url": "https://example.test/live.m3u8", "country": "US",
            "categories": ["news"],
        }])
        self.assertIn('#EXTINF:-1 tvg-id="Example.us" tvg-country="US" group-title="news",Example HD', content)
        self.assertEqual(content.count("https://example.test/live.m3u8"), 1)

    def test_build_deduplicates_and_groups_only_available_streams(self):
        streams = [
            {"channel": "One.us", "url": "https://one.test/a.m3u8"},
            {"channel": "One.us", "url": "https://one.test/a.m3u8"},
            {"channel": "Two.ca", "url": "https://two.test/a.m3u8"},
            {"channel": "Three.us", "url": "https://three.test/a.m3u8", "labels": ["Geo-blocked"]},
        ]
        channels = [
            {"id": "One.us", "name": "One", "country": "US", "categories": ["news"]},
            {"id": "Two.ca", "name": "Two", "country": "CA", "categories": ["sports"]},
            {"id": "Three.us", "name": "Three", "country": "US", "categories": ["news"]},
        ]
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(updater, "ROOT", Path(temp)), patch.object(
                updater, "available", side_effect=lambda url: ("two.test" not in url, "200")
            ):
                stats = updater.build(streams, channels, [{"code": "US", "name": "United States"}, {"code": "CA", "name": "Canada"}])
            root = Path(temp)
            all_playlist = (root / "playlists" / "all.m3u").read_text(encoding="utf-8")
            self.assertIn("One.us", all_playlist)
            self.assertNotIn("Two.ca", all_playlist)
            self.assertNotIn("Three.us", all_playlist)
            self.assertTrue((root / "playlists" / "countries" / "us-united-states.m3u").exists())
            saved_stats = json.loads((root / "stats.json").read_text(encoding="utf-8"))
            self.assertEqual(stats["duplicate_urls_removed"], 1)
            self.assertEqual(stats["available_channels"], 1)
            self.assertEqual(saved_stats["excluded_labeled_streams"], 1)


if __name__ == "__main__":
    unittest.main()
