# Global Free IPTV

Automatically refreshed playlist of publicly listed free-to-view Russian-language
IPTV streams. The daily workflow combines the [iptv-org API](https://github.com/iptv-org/api)
with Russian-language M3U playlists from [Nexonic IPTV](https://github.com/nexonic-technologies/nexonic-iptv),
[IPTV Nexus](https://github.com/dearbulut/iptv), [NGRCH IPTV](https://github.com/ngrch/iptv),
[IPTV Russia](https://github.com/substanc1/iptv-russia), and [DuckKota IPTV](https://github.com/DuckKota/iptv).
Each M3U source is parsed as a candidate list; every stream is checked before inclusion.
Temporary timeouts and server errors are retried once with a longer timeout. Duplicate
stream URLs and duplicate channel identities or normalized names are collapsed. Russian
channel aliases are used when source metadata provides them. Channel names are sorted
with Cyrillic names first, and country/category group labels are shown in Russian where
translations are available.

## Playlists

- [All available channels](playlists/all.m3u)
- [Russian-language channels](playlists/languages/rus.m3u)
- [Country playlists](playlists/countries/)
- [Category playlists](playlists/categories/)
- [Latest availability statistics](stats.json)

The generated files are refreshed daily at 09:00 Kazakhstan time (04:00 UTC)
and can also be regenerated manually with `python scripts/update_playlist.py`.
Availability checks are best-effort and reflect the workflow runner's network
location. Streams that require a supplied referrer or user agent are checked
with those headers and those headers are included in the playlist. Streams can
still be geo-restricted or temporarily unavailable from your location.

## Data source and use

Channel and stream metadata comes from the community-maintained sources linked
above. Nexonic IPTV marks its generated playlist data CC0; IPTV Nexus publishes
its repository under The Unlicense. Some community lists do not state a separate
license for their playlist metadata. This project stores stream links and channel
metadata, not video files. A listed stream is not a guarantee of availability,
ownership, or permission in every jurisdiction. Use only streams you are
authorized to access.
