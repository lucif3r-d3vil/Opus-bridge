"""End-to-end test with every upstream (TMDB, TVDB, Torrentio, stremio-server) mocked.

Run:  python3 tests/test_e2e.py
"""
import http.client
import io
import json
import os
import socket
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


ADDON, DASH = free_port(), free_port()
DATA = tempfile.mkdtemp()
os.environ.update(DATA_DIR=DATA, ADDON_PORT=str(ADDON), DASH_PORT=str(DASH),
                  ADMIN_PASSWORD="correct-horse-battery", STREMIO_URL="http://stremio-server:11470")

GOOD_TMDB = "a" * 32
GOOD_TVDB = "tvdb-secret-key-1234"
calls = []  # (url, method, headers)

real_urlopen = urllib.request.urlopen


class Resp(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(json.dumps(body).encode() if not isinstance(body, bytes) else body)
        self.status = status
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def tm_item(i, title, kind="movie", **kw):
    d = {"id": i, "overview": "plot", "poster_path": f"/p{i}.jpg", "backdrop_path": f"/b{i}.jpg", "vote_average": 8.2}
    d.update({"title": title, "release_date": "1999-03-31"} if kind == "movie" else {"name": title, "first_air_date": "2011-04-17"})
    d.update(kw)
    return d


def torrentio_streams(kind, sid):
    def s(name, title, h, idx=None, fn=None):
        o = {"name": name, "title": title, "infoHash": h}
        if idx is not None:
            o["fileIdx"] = idx
        if fn:
            o["behaviorHints"] = {"filename": fn}
        return o
    return [
        s("Torrentio\n1080p", "Movie 1080p BluRay\n\U0001F464 900 \U0001F4BE 1.86 GB ⚙️ TPB", "h1" + "0" * 38, 3, "m.1080p.mkv"),
        s("Torrentio\n4k", "Movie 4K\n\U0001F464 40 \U0001F4BE 12 GB ⚙️ X", "h2" + "0" * 38, 0),
        s("Torrentio\n720p", "Movie 720p\n\U0001F464 2 \U0001F4BE 900 MB ⚙️ X", "h3" + "0" * 38, 0),            # too few seeders
        s("Torrentio\n720p", "Movie 720p HDCAM\n\U0001F464 80 \U0001F4BE 900 MB ⚙️ X", "h4" + "0" * 38, 0),    # keyword
        s("Torrentio\n480p", "Tiny\n\U0001F464 80 \U0001F4BE 100 MB ⚙️ X", "h5" + "0" * 38, 0),               # too small
        s("Torrentio\n1080p", "Pack no idx\n\U0001F464 50 \U0001F4BE 4 GB ⚙️ X", "h6" + "0" * 38),            # needs file lookup
    ]


def fake_urlopen(req, timeout=None):
    url = req.full_url if hasattr(req, "full_url") else req
    u = urlparse(url)
    if u.hostname in ("127.0.0.1", "localhost"):
        return real_urlopen(req, timeout=timeout)
    hdrs = {k.lower(): v for k, v in dict(req.headers).items()}
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    p = u.path
    calls.append((url, req.get_method(), hdrs))

    def deny():
        raise urllib.error.HTTPError(url, 401, "unauthorized", {}, None)

    if u.hostname == "api.themoviedb.org":
        p = p[len("/3"):]
        if q.get("api_key") != GOOD_TMDB and hdrs.get("authorization") != "Bearer eyJgood":
            deny()
        if p == "/configuration":
            return Resp({"images": {}})
        if p in ("/trending/movie/week", "/movie/popular"):
            return Resp({"results": [tm_item(603, "The Matrix"), tm_item(604, "Reloaded")]})
        if p == "/discover/tv":
            assert q["with_genres"] == "16" and q["with_origin_country"] == "JP"
            return Resp({"results": [tm_item(46260, "Naruto", "tv")]})
        if p == "/search/movie":
            return Resp({"results": [tm_item(603, "The Matrix")]})
        if p == "/search/tv":
            return Resp({"results": [tm_item(1399, "Game of Thrones", "tv")]})
        if p == "/find/tt9999999":
            return Resp({"movie_results": [tm_item(777, "Hidden Gem")], "tv_results": []})
        if p == "/find/tt0133093":
            return Resp({"movie_results": [tm_item(603, "The Matrix")], "tv_results": []})
        if p == "/movie/603":
            d = tm_item(603, "The Matrix", imdb_id="tt0133093", runtime=136, genres=[{"name": "Action"}],
                        credits={"cast": [{"name": "Keanu Reeves"}], "crew": [{"job": "Director", "name": "Lana"}]},
                        images={"logos": [{"iso_639_1": "en", "file_path": "/logo.png"}]})
            return Resp(d)
        if p == "/movie/603/external_ids":
            return Resp({"imdb_id": "tt0133093"})
        if p == "/tv/1399/external_ids":
            return Resp({"imdb_id": "tt0944947"})
        if p == "/tv/1399":
            d = tm_item(1399, "Game of Thrones", "tv", genres=[{"name": "Drama"}], status="Ended",
                        last_air_date="2019-05-19", episode_run_time=[60],
                        external_ids={"imdb_id": "tt0944947"},
                        seasons=[{"season_number": 0}, {"season_number": 1}, {"season_number": 2}])
            return Resp(d)
        if p.startswith("/tv/1399/season/"):
            n = int(p.rsplit("/", 1)[1])
            return Resp({"episodes": [{"episode_number": e, "name": f"S{n}E{e}", "air_date": "2011-04-17"} for e in (1, 2)]})
        return Resp({}, 404) if False else (_ for _ in ()).throw(urllib.error.HTTPError(url, 404, "nf", {}, None))

    if u.hostname == "api4.thetvdb.com":
        if p == "/v4/login":
            body = json.loads(req.data)
            if body.get("apikey") != GOOD_TVDB:
                deny()
            return Resp({"data": {"token": "tvdbtoken"}})
        if hdrs.get("authorization") != "Bearer tvdbtoken":
            deny()
        if p == "/v4/search":
            return Resp({"data": [
                {"name": "Hidden Gem", "year": "2020", "tvdb_id": "55",
                 "remote_ids": [{"id": "tt9999999", "sourceName": "IMDB"}]},
                {"name": "The Matrix", "tvdb_id": "56", "remote_ids": [{"id": "tt0133093", "sourceName": "IMDB"}]},
                {"name": "No imdb", "tvdb_id": "57", "remote_ids": []}]})

    if u.hostname == "torrentio.test":
        if p == "/manifest.json":
            return Resp({"id": "torrentio"})
        parts = p.split("/")  # /stream/<kind>/<sid>.json
        sid = parts[3][:-5]
        if sid == "tt0944947:1:2":
            return Resp({"streams": [{"name": "Torrentio\n1080p", "title": "GoT S01 pack\n\U0001F464 100 \U0001F4BE 30 GB ⚙️ X",
                                       "infoHash": "e1" + "0" * 38}]})
        assert sid in ("tt0133093", "tt9999999"), sid
        return Resp({"streams": torrentio_streams(parts[2], sid)})

    if u.hostname == "stremio-server":
        if p == "/settings":
            return Resp({"options": []})
        if p.endswith("/create"):
            h = p.split("/")[1]
            if h.startswith("e1"):
                files = [{"name": "Game.of.Thrones.S01E01.mkv", "length": 900},
                         {"name": "Game.of.Thrones.S01E02.mkv", "length": 900},
                         {"name": "readme.txt", "length": 1}]
            else:
                files = [{"name": "sample.mkv", "length": 10}, {"name": "movie.mkv", "length": 5000}, {"name": "x.nfo", "length": 1}]
            return Resp({"files": files})
        return Resp(b"x" * 16)  # warm-up range request

    raise AssertionError("unexpected upstream call: " + url)


urllib.request.urlopen = fake_urlopen

import config  # noqa: E402
import server  # noqa: E402

config.load()
config.update({"torrentio_url": "http://torrentio.test"})
server.serve(server.Addon, ADDON)
server.serve(server.Dashboard, DASH)
time.sleep(0.3)


def addon(path):
    with real_urlopen(f"http://127.0.0.1:{ADDON}{path}", timeout=10) as r:
        return json.loads(r.read())


class Dash:
    cookie = None

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", DASH, timeout=10)
        h = dict(headers or {})
        if self.cookie:
            h["Cookie"] = self.cookie
        data = None
        if body is not None:
            data = json.dumps(body)
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, data, h)
        r = c.getresponse()
        raw = r.read()
        sc = r.getheader("Set-Cookie")
        if sc and sc.startswith("sid=") and "Max-Age=0" not in sc:
            self.cookie = sc.split(";")[0]
        c.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def post(self, path, body):
        return self.req("POST", path, body, {"X-Requested-With": "opus"})


ok = 0


def check(cond, msg):
    global ok
    assert cond, "FAIL: " + msg
    ok += 1
    print("  ok:", msg)


# --- addon manifest ------------------------------------------------------------------
m = addon("/manifest.json")
check(all(isinstance(r, dict) for r in m["resources"]), "manifest resources are objects (Gelato needs that)")
check({"catalog", "meta", "stream"} == {r["name"] for r in m["resources"]}, "manifest offers catalog, meta, stream")
check(len(m["catalogs"]) == 12 and any(c["extra"] == [{"name": "search", "isRequired": True}] for c in m["catalogs"]),
      "12 catalogs incl. search for movies and series")
check(any(c["id"] == "tmdb.anime.series" for c in m["catalogs"]), "anime catalogs present")

# --- no key yet: addon degrades instead of crashing -------------------------------------
check(addon("/catalog/movie/tmdb.movie.popular.json") == {"metas": [], "streams": []}, "no TMDB key -> empty, no crash")

# --- dashboard auth ----------------------------------------------------------------------
d = Dash()
check(d.req("GET", "/api/config")[0] == 401, "config requires login")
check(d.post("/api/login", {"password": "nope"})[0] == 401, "wrong password rejected")
check(d.req("POST", "/api/login", {"password": "correct-horse-battery"})[0] == 403, "POST without CSRF header rejected")
check(d.post("/api/login", {"password": "correct-horse-battery"})[0] == 200 and d.cookie, "login works, session cookie set")
for _ in range(6):
    Dash().post("/api/login", {"password": "bad"})
check(Dash().post("/api/login", {"password": "correct-horse-battery"})[0] == 429, "login throttled after repeated failures")

# --- keys: validated before saving, never echoed ---------------------------------------------
st, res = d.post("/api/keys", {"tmdb_key": "b" * 32})
check(st == 200 and res["results"]["tmdb_key"]["ok"] is False, "bad TMDB key rejected")
check(config.get()["tmdb_key"] == "", "bad key not saved")
st, res = d.post("/api/keys", {"tmdb_key": GOOD_TMDB, "tvdb_key": GOOD_TVDB})
check(res["results"]["tmdb_key"]["ok"] and res["results"]["tvdb_key"]["ok"], "good TMDB + TVDB keys accepted")
check(oct(stat.S_IMODE(os.stat(os.path.join(DATA, "config.json")).st_mode)) == "0o600", "config.json is chmod 600")
leak = json.dumps([d.req("GET", "/api/config")[1], d.req("GET", "/api/status")[1], d.req("GET", "/api/activity")[1]])
check(GOOD_TMDB not in leak and GOOD_TVDB not in leak, "keys never appear in any API response")
st, bearer = d.post("/api/keys", {"tmdb_key": "eyJgood"})
check(bearer["results"]["tmdb_key"]["ok"], "TMDB read-access token (Bearer) accepted")
d.post("/api/keys", {"tmdb_key": GOOD_TMDB})

# --- catalogs / search / meta --------------------------------------------------------------------
c = addon("/catalog/movie/tmdb.movie.trending.json")
check(c["metas"][0]["id"] == "tmdb:603" and c["metas"][0]["poster"].endswith("/p603.jpg"), "movie catalog returns tmdb: ids with posters")
check(addon("/catalog/series/tmdb.anime.series.json")["metas"][0]["name"] == "Naruto", "anime catalog (JP animation) works")
s = addon("/catalog/movie/tmdb.search.movie/search=matrix.json")["metas"]
check([x["id"] for x in s] == ["tmdb:603", "tmdb:777"], "search merges TVDB-only title (Hidden Gem) without duplicates")
mm = addon("/meta/movie/tmdb:603.json")["meta"]
check(mm["imdb_id"] == "tt0133093" and mm["director"] == ["Lana"] and mm["logo"].endswith("/logo.png"), "movie meta has imdb id, director, logo")
mi = addon("/meta/movie/tt0133093.json")["meta"]
check(mi["id"] == "tt0133093" and mi["moviedb_id"] == 603, "meta by IMDb id resolves via TMDB find")
sm = addon("/meta/series/tmdb:1399.json")["meta"]
check([v["id"] for v in sm["videos"]] == ["tmdb:1399:1:1", "tmdb:1399:1:2", "tmdb:1399:2:1", "tmdb:1399:2:2"],
      "series meta lists episodes (specials skipped)")

# --- streams ----------------------------------------------------------------------------------------
calls.clear()
st = addon("/stream/movie/tmdb:603.json")["streams"]
urls = [x["url"] for x in st]
check(urls[0].endswith("/h1" + "0" * 38 + "/3"), "best stream first: 1080p with most seeders")
check(any(u.endswith("/h6" + "0" * 38 + "/1") for u in urls), "torrent without fileIdx resolved to its largest video file")
check(not any("/h3" in u or "/h4" in u or "/h5" in u for u in urls), "low seeders / HDCAM / tiny files filtered out")
check(any("/h2" in u for u in urls) and urls.index(next(u for u in urls if "/h2" in u)) > 0, "4K allowed but ranked after 1080p")
time.sleep(0.3)
check(any(m == "GET" and "stremio-server" in u and "/h1" in u for u, m, _ in calls), "warm-up request sent for the top stream")
act = d.req("GET", "/api/activity")[1][0]
check(act["raw"] == 6 and act["kept"] == 3 and act["dropped"].get("seeders") == 1 and act["dropped"].get("keyword") == 1,
      "activity log explains what was dropped and why")
ep = addon("/stream/series/tmdb:1399:1:2.json")["streams"]
check(len(ep) == 1 and ep[0]["url"].endswith("/e1" + "0" * 38 + "/1"), "series episode: season-pack file picked by SxxExx (index 1)")
check(addon("/stream/movie/tt0133093.json")["streams"] != [], "streams also work for raw IMDb ids")

# --- settings --------------------------------------------------------------------------------------------
check(d.post("/api/settings", {"filters": {"min_seeders": "abc"}})[0] == 400, "invalid setting rejected")
check(d.post("/api/settings", {"filters": {"resolutions": ["8k"]}})[0] == 400, "unknown resolution rejected")
check(d.post("/api/settings", {"torrentio_url": "file:///etc/passwd"})[0] == 400, "non-http URL rejected")
check(d.post("/api/settings", {"filters": {"min_seeders": 1000, "resolutions": ["720p"]}})[0] == 200, "valid settings saved")
check(addon("/stream/movie/tmdb:603.json")["streams"] == [], "new filters apply immediately")
check("nothing passed" in d.req("GET", "/api/activity")[1][0]["note"], "empty result is explained in the log")
d.post("/api/settings", {"filters": {"min_seeders": 3, "resolutions": ["1080p", "720p", "2160p", "480p"]}})

# --- status, static files, hardening, password ------------------------------------------------------------------
sts = d.req("GET", "/api/status")[1]
check(sts["tmdb"]["ok"] and sts["tvdb"]["ok"] and sts["torrentio"]["ok"] and sts["stremio"]["ok"], "status reports every hop healthy")
check(d.req("GET", "/")[0] == 200 and d.req("GET", "/app.js")[0] == 200 and d.req("GET", "/style.css")[0] == 200, "dashboard files served")
c2 = http.client.HTTPConnection("127.0.0.1", DASH)
c2.request("GET", "/../server.py")
check(c2.getresponse().status == 404, "path traversal blocked")
check(d.post("/api/password", {"current": "wrong", "new": "x" * 12})[0] == 403, "password change needs the current password")
check(d.post("/api/password", {"current": "correct-horse-battery", "new": "short"})[0] == 400, "short new password rejected")
check(d.post("/api/password", {"current": "correct-horse-battery", "new": "a-much-longer-passphrase"})[0] == 200, "password changed")
config._fails.clear()  # the throttle check above locked 127.0.0.1 on purpose
check(Dash().post("/api/login", {"password": "a-much-longer-passphrase"})[0] == 200, "new password works")
check(d.post("/api/keys", {"clear_tvdb_key": True})[1]["results"]["tvdb_key"]["ok"] and config.get()["tvdb_key"] == "", "TVDB key can be removed")

print(f"\nALL {ok} CHECKS PASSED")
