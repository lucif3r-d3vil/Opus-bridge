<div align="center">

# Opus Bridge

**Search any movie, series or anime in Jellyfin. Press play. It streams.**
No debrid account.

[![Build image](https://github.com/lucif3r-d3vil/opus-bridge/actions/workflows/docker.yml/badge.svg)](https://github.com/lucif3r-d3vil/opus-bridge/actions/workflows/docker.yml)
![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-stdlib%20only-blue)
![Docker](https://img.shields.io/badge/docker%20compose-ready-2496ED)

</div>

---

Opus Bridge turns Jellyfin into a Netflix-style front end for torrent streams. It plugs into the [Gelato](https://github.com/lostb1t/Gelato) plugin, gives it catalogs, search, posters and episode lists from **TMDB and TVDB**, and finds playable torrents through **Torrentio**. [`stremio-server`](https://hub.docker.com/r/stremio/server) turns the chosen torrent into a video Jellyfin can play, as it downloads.

You get a small web dashboard to paste your keys, tune seeders / size / quality, and see exactly why a title did or didn't play.

```
 You search "Dune" in Jellyfin
        │
        ▼
 Jellyfin + Gelato ───────► Opus Bridge ───────► TMDB / TVDB   posters, search, episodes
                                │
                                ├──────────────► Torrentio     "which torrents have this?"
                                │
                                └──────────────► stremio-server  torrent ➜ video stream
                                                        │
 Jellyfin plays the stream  ◄───────────────────────────┘
```

> **Heads up.** Without a debrid service your IP address joins public torrent swarms. Use this only for content you have the right to access, and run `stremio-server` behind a VPN (see [Use a VPN](#use-a-vpn)).

## What you get

- **Search that just works.** TMDB search for movies, series and anime, plus TVDB for titles TMDB misses.
- **Browse rows in Jellyfin.** Trending, popular, top rated and new releases, airing series, anime series and anime movies.
- **Your rules, not the tracker's.** Minimum seeders, size limits, allowed qualities in your order, blocked keywords like CAM.
- **Faster starts.** A warm-up fetches the best torrent when you open a title, so playback begins sooner.
- **A dashboard that explains itself.** Health of every hop, and a log such as *"Torrentio returned 41, your filters kept 3"*.
- **Small and safe.** Pure Python standard library, one container, password-protected dashboard, keys never leave your server.

## What you need

| | Required | Notes |
|---|---|---|
| A Linux box or NAS with **Docker + Docker Compose v2** | ✅ | `docker compose version` should print a version. |
| **Jellyfin 10.11 or newer** | ✅ | Tested on 12.3. The compose file below can start one for you. |
| **[Gelato](https://github.com/lostb1t/Gelato) plugin** in Jellyfin | ✅ | Install it from its own README, then restart Jellyfin. |
| **TMDB API key** | ✅ | Free. [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api). Either the v3 key or the read token works. |
| **TVDB API key** | optional | Free tier: [thetvdb.com/api-information](https://thetvdb.com/api-information). Improves search for some titles. |
| **A VPN** | strongly advised | Needed for torrent traffic privacy. |
| Spare **disk space** for the torrent cache | ✅ | `stremio-server` caches what you watch. |

## Install

### Option A: I have nothing yet (Jellyfin + everything)

1. Make a folder and save this as `docker-compose.yml`. Change the two media paths (or delete those lines if you have no local library).

```yaml
services:
  jellyfin:
    image: jellyfin/jellyfin:latest
    container_name: jellyfin
    restart: unless-stopped
    ports:
      - "8096:8096"
    environment:
      - TZ=${TZ:-UTC}
    volumes:
      - jellyfin-config:/config
      - jellyfin-cache:/cache
      - gelato-data:/gelato                 # where Gelato keeps its placeholder items
      - /path/to/your/movies:/movies:ro     # optional: your own files
      - /path/to/your/tv:/tv:ro             # optional: your own files

  stremio-server:
    image: stremio/server:latest
    container_name: stremio-server
    restart: unless-stopped
    # No ports published: only containers on this Docker network can reach it.

  opus-bridge:
    build: https://github.com/lucif3r-d3vil/opus-bridge.git#main
    image: opus-bridge:local
    container_name: opus-bridge
    restart: unless-stopped
    depends_on:
      - stremio-server
    environment:
      - TZ=${TZ:-UTC}
      - STREMIO_URL=http://stremio-server:11470
      # Optional first-boot values. Everything can be set later in the dashboard.
      # - ADMIN_PASSWORD=choose-a-long-one
      # - TMDB_API_KEY=
      # - TVDB_API_KEY=
    volumes:
      - opus-data:/data
    ports:
      # Dashboard. This machine only by default. Use your LAN IP (e.g. 192.168.1.10:7001:7001) to reach it from other devices.
      - "127.0.0.1:7001:7001"
    # Port 7000 (the addon) is deliberately not published.

volumes:
  jellyfin-config:
  jellyfin-cache:
  gelato-data:
  opus-data:
```

2. Start it:

```bash
docker compose up -d
docker logs opus-bridge        # the first run prints your dashboard password ONCE
```

### Option B: I already run Jellyfin

The bridge and Jellyfin only need to share a Docker network so Jellyfin can reach `http://opus-bridge:7000`.

1. Find Jellyfin's network name:

```bash
docker inspect jellyfin --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}'
```

2. Save this as `docker-compose.yml` in a new folder, replacing `YOUR_JELLYFIN_NETWORK` with that name:

```yaml
services:
  stremio-server:
    image: stremio/server:latest
    container_name: stremio-server
    restart: unless-stopped

  opus-bridge:
    build: https://github.com/lucif3r-d3vil/opus-bridge.git#main
    image: opus-bridge:local
    container_name: opus-bridge
    restart: unless-stopped
    depends_on:
      - stremio-server
    environment:
      - TZ=${TZ:-UTC}
      - STREMIO_URL=http://stremio-server:11470
    volumes:
      - opus-data:/data
    ports:
      - "127.0.0.1:7001:7001"

volumes:
  opus-data:

networks:
  default:
    name: YOUR_JELLYFIN_NETWORK
    external: true
```

3. Start it and read the password:

```bash
docker compose up -d
docker logs opus-bridge
```

Already run `stremio-server`? Skip that service and keep `STREMIO_URL` pointing at yours.

<details>
<summary>Run <code>stremio-server</code> on its own (standalone compose)</summary>

```yaml
services:
  stremio-server:
    image: stremio/server:latest
    container_name: stremio-server
    restart: unless-stopped
    environment:
      - APP_PATH=/config          # keeps the torrent cache across restarts
    volumes:
      - stremio-config:/config
    # The server listens on 11470 inside the container. Only publish it if
    # something outside Docker needs it:
    # ports:
    #   - "127.0.0.1:11470:11470"

volumes:
  stremio-config:
```

</details>

## Set it up (about 3 minutes)

**1. Open the dashboard.** Go to `http://127.0.0.1:7001` (or your LAN IP) and sign in with the password from `docker logs opus-bridge`. Change it under **Security**.

**2. Add your keys.** Open **Settings**, paste your TMDB key (and TVDB if you have one) and press **Test and save**. The bridge checks each key against TMDB and TVDB before keeping it. The **Pipeline** page should now show four green dots.

**3. Connect Gelato.** In Jellyfin go to **Dashboard → Plugins → Gelato**:

- **Addon** tab: paste `http://opus-bridge:7000/manifest.json`, press **Test**, then **Save**. This is the only address Gelato needs.
- Set Gelato's movie and series folders to `/gelato/movies` and `/gelato/shows`, and add those folders to a Jellyfin Movies library and a Shows library (Gelato's own README is the authority on its settings).

**4. Play something.** Search for a title in Jellyfin, open it, press play. The first start can take 10 to 30 seconds while peers are found. Watch **Recent stream requests** on the bridge's Pipeline page to see what happened.

## Tune it (Settings page)

| Setting | What it does | Default |
|---|---|---|
| Qualities, in order | Which resolutions are allowed and which wins | 1080p, 720p, 2160p, 480p |
| Minimum seeders | Drops dead torrents | 3 |
| Movie size | Smallest and largest movie file | 500 MB to 20 GB |
| Episode / season size | Same for series | 100 MB to 80 GB |
| Streams offered | How many options appear per title | 10 |
| Blocked keywords | Skips CAM, telesync, screeners and the like | `cam,hdcam,telesync,hdts,screener,workprint,telecine` |
| Warm-up | Pre-fetches the top torrent when a title opens | on |
| Service URLs | Torrentio and stremio-server addresses | public Torrentio, `stremio-server:11470` |

## Reverse proxy, LAN only (Traefik example)

Put the **dashboard** (port 7001) behind your proxy if you like. Never put port 7000 or the dashboard on the internet. This example lets only your LAN in:

```yaml
  opus-bridge:
    # ...same as above, without the `ports:` section...
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.opus-bridge.rule=Host(`bridge.example.lan`)"
      - "traefik.http.routers.opus-bridge.entrypoints=websecure"
      - "traefik.http.routers.opus-bridge.tls=true"
      - "traefik.http.routers.opus-bridge.middlewares=opus-bridge-lan"
      - "traefik.http.middlewares.opus-bridge-lan.ipallowlist.sourcerange=192.168.1.0/24"
      - "traefik.http.services.opus-bridge.loadbalancer.server.port=7001"
```

Use your own host name and LAN range. Traefik v2 calls the middleware `ipwhitelist`.

## Use a VPN

The simplest way is to run `stremio-server` inside a [Gluetun](https://github.com/qdm12/gluetun) container's network. Configure Gluetun for your VPN provider (see its wiki), then:

```yaml
  stremio-server:
    image: stremio/server:latest
    network_mode: "service:gluetun"
```

The bridge then reaches it at `http://gluetun:11470`, so change **Stremio server URL** in Settings.

## Troubleshooting

| What you see | What to do |
|---|---|
| Gelato says **"The addon didn't answer"** | Jellyfin can't reach the bridge. Both must share a Docker network: `docker exec jellyfin curl -s http://opus-bridge:7000/manifest.json` should print JSON. Use `http://opus-bridge:7000/manifest.json`, one address only. |
| Browse rows or search are **empty** | The TMDB key isn't saved. Settings, then **Test and save**. |
| Plays but **takes a while** | Normal for the first start of a torrent (10 to 30 s). Keep warm-up on. |
| **"No streams"** for a title | Open **Recent stream requests**: it shows how many torrents Torrentio returned and which filter dropped them. Lower the minimum seeders or allow more qualities. |
| Pipeline shows **Torrentio unreachable** | Some ISPs block the public instance. Put another Torrentio-compatible URL in Settings, or use a VPN. |
| **Lost the dashboard password** | `docker compose stop opus-bridge && docker volume rm <project>_opus-data`, then start again for a new first-run password. This also clears your saved keys and settings. |

## Security

- The addon port (7000) is never published. Only containers on the same Docker network can reach it.
- The dashboard needs a password, stored as a PBKDF2 hash, with login rate limiting and 12-hour sessions.
- API keys are validated before saving, stored in `/data/config.json` with mode 600, and never returned by any API call or written to logs.
- Strict Content-Security-Policy, `SameSite=Strict` cookies and a request-header check protect the dashboard.
- Keep the dashboard on your LAN. Do not forward it to the internet.

## Limits to know about

- Anime uses TMDB's season and episode numbering mapped to IMDb ids. Long-running shows that release by absolute number (episode 1050, say) can miss. Kitsu and MAL ids are not supported.
- TVDB is used for search only. Episode data comes from TMDB.
- Quality depends on what is seeded. Rare or very new titles may have nothing healthy.
- Gelato creates placeholder items in Jellyfin as titles are discovered. They are not real files and take a little database space.

## Develop

```bash
python3 tests/test_e2e.py     # whole stack against mocked TMDB, TVDB, Torrentio and stremio-server
```

Pure Python standard library, no dependencies. `.github/workflows/docker.yml` builds amd64 and arm64 images to `ghcr.io/lucif3r-d3vil/opus-bridge`. Issues and pull requests are welcome.

## License

MIT. Use responsibly, and only for content you are entitled to watch.
