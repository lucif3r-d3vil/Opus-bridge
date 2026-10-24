"""Stremio addon catalog / meta layer built on TMDB (+ TVDB for search)."""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, unquote

from providers import Tmdb, Tvdb
from util import ApiError, log

IMG = "https://image.tmdb.org/t/p"
PAGE = 20

# (catalog id, stremio type, display name, tmdb path, extra tmdb params)
CATALOGS = [
    ("tmdb.movie.trending", "movie", "Trending Movies", "/trending/movie/week", {}),
    ("tmdb.movie.popular", "movie", "Popular Movies", "/movie/popular", {}),
    ("tmdb.movie.top", "movie", "Top Rated Movies", "/movie/top_rated", {}),
    ("tmdb.movie.new", "movie", "New Releases", "/movie/now_playing", {}),
    ("tmdb.anime.movies", "movie", "Anime Movies", "/discover/movie",
     {"with_genres": "16", "with_original_language": "ja", "sort_by": "popularity.desc"}),
    ("tmdb.series.trending", "series", "Trending Series", "/trending/tv/week", {}),
    ("tmdb.series.popular", "series", "Popular Series", "/tv/popular", {}),
    ("tmdb.series.top", "series", "Top Rated Series", "/tv/top_rated", {}),
    ("tmdb.series.airing", "series", "Airing Now", "/tv/on_the_air", {}),
    ("tmdb.anime.series", "series", "Anime Series", "/discover/tv",
     {"with_genres": "16", "with_origin_country": "JP", "sort_by": "popularity.desc"}),
]
SEARCH_IDS = {"movie": "tmdb.search.movie", "series": "tmdb.search.series"}


def manifest(version="1.0.0"):
    cats = []
    for cid, typ, name, _, _ in CATALOGS:
        cats.append({"id": cid, "type": typ, "name": name, "extra": [{"name": "skip"}]})
    for typ, cid in SEARCH_IDS.items():
        cats.append({
            "id": cid, "type": typ,
            "name": "Search " + ("Movies" if typ == "movie" else "Series & Anime"),
            "extra": [{"name": "search", "isRequired": True}],
        })
    types = ["movie", "series"]
    return {
        "id": "xyz.opusgrid.opus-bridge",
        "version": version,
        "name": "Opus Bridge",
        "description": "TMDB/TVDB catalogs for movies, series and anime; streams from Torrentio via the local Stremio server.",
        "resources": [
            {"name": "catalog", "types": types},
            {"name": "meta", "types": types, "idPrefixes": ["tmdb:", "tt"]},
            {"name": "stream", "types": types, "idPrefixes": ["tmdb:", "tt"]},
        ],
        "types": types,
        "idPrefixes": ["tmdb:", "tt"],
        "catalogs": cats,
        "behaviorHints": {"configurable": False},
    }


def _img(path, size):
    return f"{IMG}/{size}{path}" if path else None


def _year(date):
    return (date or "")[:4] or None


def _preview(item, typ):
    title = item.get("title") if typ == "movie" else item.get("name")
    date = item.get("release_date") if typ == "movie" else item.get("first_air_date")
    p = {
        "id": f"tmdb:{item['id']}",
        "type": typ,
        "name": title or item.get("original_title") or item.get("original_name") or "Untitled",
        "poster": _img(item.get("poster_path"), "w500"),
        "background": _img(item.get("backdrop_path"), "w1280"),
        "description": item.get("overview") or "",
        "releaseInfo": _year(date),
    }
    if item.get("vote_average"):
        p["imdbRating"] = f"{item['vote_average']:.1f}"
    return {k: v for k, v in p.items() if v not in (None, "")}


def parse_extra(extra):
    """'search=foo%20bar&skip=20' -> {'search': 'foo bar', 'skip': '20'}"""
    out = {}
    for k, v in parse_qs(unquote(extra or ""), keep_blank_values=True).items():
        out[k] = v[0]
    return out


def catalog(typ, cid, extra):
    ex = parse_extra(extra)
    skip = int(ex.get("skip", "0") or 0)
    page = skip // PAGE + 1
    if cid in SEARCH_IDS.values():
        return {"metas": search(typ, ex.get("search", ""))}
    for c_id, c_typ, _, path, params in CATALOGS:
        if c_id == cid and c_typ == typ:
            data = Tmdb.get(path, 3 * 3600, page=page, **params)
            return {"metas": [_preview(i, typ) for i in data.get("results", [])]}
    return {"metas": []}


def search(typ, query):
    query = query.strip()
    if not query:
        return []
    path = "/search/movie" if typ == "movie" else "/search/tv"
    data = Tmdb.get(path, 1800, query=query, include_adult="false")
    metas = [_preview(i, typ) for i in data.get("results", [])]
    seen = {m["id"] for m in metas}
    if Tvdb.enabled():
        try:
            for extra_meta in _tvdb_extra(typ, query, seen):
                metas.append(extra_meta)
                seen.add(extra_meta["id"])
        except ApiError as e:
            log("tvdb search skipped:", e)
    return metas


def _tvdb_extra(typ, query, seen):
    """Titles TVDB knows that TMDB's search missed, mapped back to TMDB through IMDb ids."""
    hits = Tvdb.search(query, "movie" if typ == "movie" else "series")[:5]
    found = []

    def lookup(h):
        r = Tmdb.get(f"/find/{h['imdb']}", 86400, external_source="imdb_id")
        rows = r.get("movie_results" if typ == "movie" else "tv_results") or []
        return _preview(rows[0], typ) if rows else None

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(lookup, h) for h in hits]
        for f in as_completed(futs, timeout=8):
            try:
                m = f.result()
            except ApiError:
                continue
            if m and m["id"] not in seen and m not in found:
                found.append(m)
    return found


# ---- id resolution -------------------------------------------------------------------


def split_id(raw):
    """'tmdb:123:1:2' -> ('tmdb','123',1,2); 'tt0133093:1:2' -> ('imdb','tt0133093',1,2)"""
    raw = unquote(raw)
    parts = raw.split(":")
    if parts[0] == "tmdb" and len(parts) >= 2:
        s = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        e = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None
        return "tmdb", parts[1], s, e
    if re.fullmatch(r"tt\d+", parts[0]):
        s = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        e = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        return "imdb", parts[0], s, e
    return None, raw, None, None


def to_tmdb_id(typ, kind, ident):
    if kind == "tmdb":
        return ident
    r = Tmdb.get(f"/find/{ident}", 86400, external_source="imdb_id")
    rows = r.get("movie_results" if typ == "movie" else "tv_results") or []
    return str(rows[0]["id"]) if rows else None


def imdb_for(typ, kind, ident):
    """IMDb id for a movie/series (the id Torrentio needs)."""
    if kind == "imdb":
        return ident
    if typ == "movie":
        return Tmdb.get(f"/movie/{ident}/external_ids", 7 * 86400).get("imdb_id") or None
    return Tmdb.get(f"/tv/{ident}/external_ids", 7 * 86400).get("imdb_id") or None


# ---- meta ------------------------------------------------------------------------------


def meta(typ, raw_id):
    kind, ident, _, _ = split_id(raw_id)
    if kind is None:
        return {"meta": None}
    tid = to_tmdb_id(typ, kind, ident)
    if not tid:
        return {"meta": None}
    return {"meta": _movie_meta(raw_id, tid) if typ == "movie" else _series_meta(raw_id, tid)}


def _common(raw_id, typ, d, date, imdb):
    cast = [c["name"] for c in (d.get("credits", {}).get("cast") or [])[:10]]
    crew = (d.get("credits", {}).get("crew") or [])
    m = {
        "id": raw_id,
        "type": typ,
        "name": d.get("title") or d.get("name"),
        "poster": _img(d.get("poster_path"), "w500"),
        "background": _img(d.get("backdrop_path"), "w1280"),
        "description": d.get("overview") or "",
        "releaseInfo": _year(date),
        "released": (date + "T00:00:00.000Z") if date else None,
        "genres": [g["name"] for g in d.get("genres") or []],
        "cast": cast,
        "imdb_id": imdb,
        "moviedb_id": d.get("id"),
    }
    if d.get("vote_average"):
        m["imdbRating"] = f"{d['vote_average']:.1f}"
    logos = (d.get("images") or {}).get("logos") or []
    en = [l for l in logos if l.get("iso_639_1") in ("en", None)] or logos
    if en:
        m["logo"] = _img(en[0].get("file_path"), "w500")
    return m, crew


def _movie_meta(raw_id, tid):
    d = Tmdb.get(f"/movie/{tid}", 6 * 3600, append_to_response="credits,images",
                 include_image_language="en,null")
    m, crew = _common(raw_id, "movie", d, d.get("release_date"), d.get("imdb_id"))
    m["runtime"] = f"{d['runtime']} min" if d.get("runtime") else None
    m["director"] = [c["name"] for c in crew if c.get("job") == "Director"]
    return {k: v for k, v in m.items() if v not in (None, "", [])}


def _series_meta(raw_id, tid):
    d = Tmdb.get(f"/tv/{tid}", 6 * 3600, append_to_response="credits,images,external_ids",
                 include_image_language="en,null")
    imdb = (d.get("external_ids") or {}).get("imdb_id")
    m, _ = _common(raw_id, "series", d, d.get("first_air_date"), imdb)
    last = d.get("last_air_date")
    m["releaseInfo"] = (_year(d.get("first_air_date")) or "") + (
        "–" if d.get("status") == "Ended" and last and _year(last) != _year(d.get("first_air_date")) else "")
    m["runtime"] = f"{d['episode_run_time'][0]} min" if d.get("episode_run_time") else None
    prefix = m["id"]
    seasons = [s["season_number"] for s in d.get("seasons") or [] if s.get("season_number", 0) > 0]
    videos = []

    def one(n):
        try:
            return n, Tmdb.get(f"/tv/{tid}/season/{n}", 6 * 3600)
        except ApiError:
            return n, {}

    with ThreadPoolExecutor(max_workers=6) as ex:
        for n, sd in sorted(ex.map(one, seasons)):
            for ep in sd.get("episodes") or []:
                videos.append({
                    "id": f"{prefix}:{n}:{ep['episode_number']}",
                    "title": ep.get("name") or f"Episode {ep['episode_number']}",
                    "season": n,
                    "episode": ep["episode_number"],
                    "released": (ep["air_date"] + "T00:00:00.000Z") if ep.get("air_date") else None,
                    "overview": ep.get("overview") or "",
                    "thumbnail": _img(ep.get("still_path"), "w300"),
                })
    m["videos"] = [{k: v for k, v in vid.items() if v not in (None, "")} for vid in videos]
    return {k: v for k, v in m.items() if v not in (None, "", [])}
