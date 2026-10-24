# Opus Bridge

Search and play movies, series and anime from Jellyfin, streamed from torrents on demand, with no debrid service.

It sits between Jellyfin's [Gelato](https://github.com/lostb1t/Gelato) plugin and the internet:

```
Jellyfin + Gelato ──> Opus Bridge ──> TMDB / TVDB   (catalogs, search, posters, episodes)
                          │
                          └────────> Torrentio      (finds torrents for a title)
                          └────────> stremio-server (turns a torrent into a video stream)
```

You get one dashboard to enter your TMDB and TVDB keys, tune seeders / size / quality filters, and see why a title did or didn't play.

> Use it only for content you have the right to access. Without a debrid service your IP address joins public torrent swarms, so running `stremio-server` behind a VPN is strongly recommended (see below).

## Quick start

Requirements: Docker with Compose, a free [TMDB API key](https://www.themoviedb.org/settings/api), and Jellyfin 10.11 or newer with the Gelato plugin installed.

```bash
git clone <this repo> opus-bridge && cd opus-bridge
cp .env.example .env          # optional: edit values
docker compose up -d
docker logs opus-bridge       # first run prints the admin password once
```

1. Open `http://127.0.0.1:7001`, sign in, go to **Settings**, paste your TMDB key (and optionally a TVDB key) and press **Test and save**.
2. In Jellyfin: Dashboard, Plugins, Gelato, Addon. Paste the address shown on the dashboard's **Pipeline** page (`http://opus-bridge:7000/manifest.json`) and press **Test**, then **Save**.
3. Search for a title in Jellyfin and press play.

Jellyfin must be able to reach the bridge by name, so both need to share a Docker network. If Jellyfin already runs in its own compose project, add to `.env`:

```
COMPOSE_FILE=docker-compose.yml:docker-compose.shared-network.yml
JELLYFIN_NETWORK=<name from `docker network ls`>
```

then run `docker compose up -d` again.

To open the dashboard from another device, set `DASHBOARD_BIND` in `.env` to the server's LAN IP. Never forward that port to the internet.

## What it provides

| | |
|---|---|
| Catalogs | Trending, popular, top rated and new releases for movies; trending, popular, top rated and airing now for series; anime series and anime movies (TMDB animation from Japan). |
| Search | TMDB search for movies and series (anime included). If a TVDB key is set, titles TVDB knows that TMDB's search misses are added. |
| Metadata | Posters, backgrounds, logos, cast, episode lists with stills. |
| Streams | Torrentio results filtered by your rules, best first, played through `stremio-server`. |
| Warm-up | Starts fetching the best torrent when a title is opened so playback begins sooner. |
| Dashboard | Service health for every hop, keys form, filters, and a log of each stream request showing how many torrents were dropped and why. |

### Filters (all in the dashboard)

Allowed qualities and their preference order, minimum seeders, movie size range, episode and season-pack size range, blocked keywords (CAM and similar), and how many streams to offer. For torrents where Torrentio doesn't say which file to play, the bridge asks `stremio-server` for the file list and picks the largest video (movies) or the matching `SxxExx` file (series).

## Security

- The addon port (7000) is not published. Only containers on the same Docker network can reach it.
- The dashboard needs a password. It is generated on first run (or taken from `ADMIN_PASSWORD`), stored as a PBKDF2 hash, and logins are rate limited.
- API keys are checked against TMDB and TVDB before saving, stored in `/data/config.json` with mode 600, and are never returned by any API call or written to logs.
- The dashboard sets a strict Content-Security-Policy and uses `SameSite=Strict` cookies plus a request header check.

## Routing torrent traffic through a VPN

The simplest way is to run `stremio-server` inside a [Gluetun](https://github.com/qdm12/gluetun) container's network:

```yaml
  stremio-server:
    network_mode: "service:gluetun"
```

In that case the bridge reaches it at `http://gluetun:11470`, so change **Stremio server URL** in Settings. Gluetun's configuration depends on your VPN provider; follow its wiki.

## Limits to know about

- Anime uses TMDB's season and episode numbering mapped to IMDb ids, which Torrentio understands for most shows. Long-running series that releases number absolutely (for example episode 1050) can miss; Kitsu/MAL id support is not included.
- TVDB is used for search only. Episode data comes from TMDB.
- The first play of a torrent can take 10 to 30 seconds while peers are found. Warm-up reduces this but cannot remove it.
- Torrentio's public instance is sometimes blocked by ISPs. You can point the dashboard at another Torrentio-compatible URL.

## Development

```bash
python3 tests/test_e2e.py     # runs the whole stack against mocked TMDB, TVDB, Torrentio and stremio-server
```

The app is pure Python standard library, no dependencies. Images are built for amd64 and arm64 by `.github/workflows/docker.yml` and published to `ghcr.io/<owner>/<repo>`; to use the published image instead of building locally, replace `build: .` with `image: ghcr.io/<owner>/<repo>:latest` in `docker-compose.yml`.

## License

MIT
