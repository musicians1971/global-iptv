# Global Free IPTV

Automatically refreshed playlist of publicly listed free-to-view Russian-language
IPTV streams. The daily workflow fetches the [iptv-org API](https://github.com/iptv-org/api),
keeps only feeds explicitly marked as Russian (rus), checks stream responses,
removes duplicate URLs, and groups results by country and category when metadata
is available.

## Playlists

- [All available channels](playlists/all.m3u)
- [Russian-language channels](playlists/languages/rus.m3u)
- [Country playlists](playlists/countries/)
- [Category playlists](playlists/categories/)
- [Latest availability statistics](stats.json)

The generated files are refreshed daily at 09:00 Kazakhstan time (04:00 UTC)
and can also be regenerated manually with the command python scripts/update_playlist.py.
Availability checks are best-effort and reflect the workflow runner's network
location. Streams that require a supplied referrer or user agent are checked
with those headers and those headers are included in the playlist. Streams can
still be geo-restricted or temporarily unavailable from your location.

## Data source and use

Channel and stream metadata comes from the community-maintained
[iptv-org API](https://github.com/iptv-org/api). A listed stream is not a
guarantee of availability, ownership, or permission in every jurisdiction.
Use only streams you are authorized to access.
