"""Two HTTP servers in one process.

  :7000  Stremio addon (internal; Gelato talks to this, never publish it)
  :7001  Dashboard + admin API (password protected; publish this one on your LAN only)
"""
import json
import mimetypes
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import catalog
import config
import streams
import subtitles
from providers import Tmdb, Tvdb, cache
from util import ApiError, http_request, log

VERSION = "1.0.0"
ADDON_PORT = int(os.environ.get("ADDON_PORT", "7000"))
DASH_PORT = int(os.environ.get("DASH_PORT", "7001"))
ADDON_URL = os.environ.get("ADDON_URL", f"http://opus-bridge:{ADDON_PORT}/manifest.json")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard")


class Base(BaseHTTPRequestHandler):
    server_version = "opus-bridge"
    sys_version = ""

    def _send(self, code, body, ctype="application/json", extra=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *a):  # keep logs quiet and free of query strings
        pass


# ----------------------------------------------------------------------------- addon


class Addon(Base):
    def _send(self, code, body, ctype="application/json", extra=None):
        h = {"Access-Control-Allow-Origin": "*", "Cache-Control": "max-age=60"}
        h.update(extra or {})
        super()._send(code, body, ctype, h)

    def do_OPTIONS(self):
        self._send(204, b"", extra={"Access-Control-Allow-Headers": "*"})

    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p == "/health":
                return self._send(200, {"ok": True})
            if p == "/manifest.json":
                return self._send(200, catalog.manifest(VERSION), extra={"Cache-Control": "no-cache"})
            m = re.fullmatch(r"/catalog/(movie|series)/([^/]+?)(?:/([^/]+))?\.json", p)
            if m:
                return self._send(200, catalog.catalog(m.group(1), unquote(m.group(2)), m.group(3) or ""))
            m = re.fullmatch(r"/meta/(movie|series)/([^/]+)\.json", p)
            if m:
                return self._send(200, catalog.meta(m.group(1), m.group(2)))
            m = re.fullmatch(r"/stream/(movie|series)/([^/]+)\.json", p)
            if m:
                return self._send(200, {"streams": streams.build(m.group(1), unquote(m.group(2)))},
                                  extra={"Cache-Control": "max-age=300"})
            # /subtitles/<type>/<id>.json, optionally /subtitles/<type>/<id>/<extra>.json (filename=..., videoHash=...)
            m = re.fullmatch(r"/subtitles/(movie|series)/([^/]+?)(?:/[^/]+)?\.json", p)
            if m:
                subs = subtitles.for_request(m.group(1), unquote(m.group(2)))
                return self._send(200, {"subtitles": subs},
                                  extra={"Cache-Control": "max-age=300" if subs else "no-cache"})
        except ApiError as e:
            log("addon error:", self.path.split("?")[0], e)
            return self._send(200, {"metas": [], "streams": []}, extra={"Cache-Control": "no-cache"})
        except Exception as e:  # never let a bug take the addon down
            log("addon bug:", type(e).__name__, e)
            return self._send(200, {"metas": [], "streams": []}, extra={"Cache-Control": "no-cache"})
        self._send(404, {"error": "not found"})


# --------------------------------------------------------------------------- dashboard

SEC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}
_status_cache = {"t": 0, "v": None}


def _service_status():
    now = time.time()
    if _status_cache["v"] and now - _status_cache["t"] < 20:
        return _status_cache["v"]
    cfg = config.get()

    def stremio():
        try:
            http_request(cfg["stremio_url"].rstrip("/") + "/settings", timeout=4)
            return {"ok": True, "detail": "reachable"}
        except ApiError as e:
            return {"ok": False, "detail": f"unreachable ({e})"}

    def torrentio():
        try:
            http_request(cfg["torrentio_url"].rstrip("/") + "/manifest.json", timeout=6)
            return {"ok": True, "detail": "reachable"}
        except ApiError as e:
            return {"ok": False, "detail": f"unreachable ({e})"}

    def tmdb():
        if not cfg["tmdb_key"]:
            return {"ok": False, "detail": "no key set"}
        ok, msg = Tmdb.validate(cfg["tmdb_key"])
        return {"ok": ok, "detail": "key works" if ok else msg}

    def tvdb():
        if not cfg["tvdb_key"]:
            return {"ok": None, "detail": "optional, not set"}
        ok, msg = Tvdb.validate(cfg["tvdb_key"], cfg["tvdb_pin"])
        return {"ok": ok, "detail": "key works" if ok else msg}

    with ThreadPoolExecutor(max_workers=4) as ex:
        f = {n: ex.submit(fn) for n, fn in
             (("stremio", stremio), ("torrentio", torrentio), ("tmdb", tmdb), ("tvdb", tvdb))}
        val = {n: x.result() for n, x in f.items()}
    _status_cache.update(t=now, v=val)
    return val


def _clean_filters(f, cur):
    out = dict(cur)

    def num(key, lo, hi, cast=float):
        if key in f:
            try:
                v = cast(f[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a number")
            if not lo <= v <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}")
            out[key] = v

    num("min_seeders", 0, 10000, int)
    num("movie_min_mb", 0, 100000)
    num("movie_max_gb", 0.1, 500)
    num("series_min_mb", 0, 100000)
    num("series_max_gb", 0.1, 1000)
    num("max_streams", 1, 50, int)
    if "resolutions" in f:
        rs = f["resolutions"]
        if not isinstance(rs, list) or not all(r in config.RESOLUTIONS for r in rs) or not rs:
            raise ValueError("resolutions must be a non-empty list of " + ", ".join(config.RESOLUTIONS))
        out["resolutions"] = rs
    if "exclude_keywords" in f:
        out["exclude_keywords"] = str(f["exclude_keywords"])[:300]
    if "resolve_missing_fileidx" in f:
        out["resolve_missing_fileidx"] = bool(f["resolve_missing_fileidx"])
    return out


def _clean_subtitles(s, cur):
    if not isinstance(s, dict):
        raise ValueError("subtitles must be an object")
    out = dict(cur)
    if "enabled" in s:
        out["enabled"] = bool(s["enabled"])
    if "languages" in s:
        out["languages"] = subtitles.clean_languages(s["languages"])
    if "per_language" in s:
        try:
            n = int(s["per_language"])
        except (TypeError, ValueError):
            raise ValueError("per_language must be a number")
        if not 1 <= n <= 5:
            raise ValueError("per_language must be between 1 and 5")
        out["per_language"] = n
    if "source_url" in s:
        v = str(s["source_url"]).strip().rstrip("/")
        if not re.fullmatch(r"https?://[^\s]+", v):
            raise ValueError("source_url must be an http(s) URL")
        out["source_url"] = v
    if out["enabled"] and not out["languages"]:
        raise ValueError("add at least one language, or turn subtitles off")
    return out


class Dashboard(Base):
    def _send(self, code, body, ctype="application/json", extra=None):
        h = dict(SEC_HEADERS)
        h.update(extra or {})
        super()._send(code, body, ctype, h)

    def _cookie(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c["sid"].value if "sid" in c else None

    def _authed(self):
        return config.valid_session(self._cookie())

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 20000:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        p = self.path.split("?")[0]
        if p.startswith("/api/"):
            if p == "/api/session":
                return self._send(200, {"authed": self._authed(), "version": VERSION})
            if not self._authed():
                return self._send(401, {"error": "login required"})
            if p == "/api/config":
                c = config.masked()
                c["addon_url"] = ADDON_URL
                c["resolution_options"] = config.RESOLUTIONS
                return self._send(200, c)
            if p == "/api/status":
                return self._send(200, _service_status())
            if p == "/api/activity":
                return self._send(200, list(streams.activity)[:100])
            return self._send(404, {"error": "not found"})
        name = "index.html" if p in ("/", "") else p.lstrip("/")
        path = os.path.normpath(os.path.join(WEB_DIR, name))
        if not path.startswith(WEB_DIR + os.sep) or not os.path.isfile(path):
            return self._send(404, b"not found", "text/plain")
        with open(path, "rb") as f:
            body = f.read()
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return self._send(200, body, ctype + ("; charset=utf-8" if ctype.startswith("text/") or "javascript" in ctype else ""))

    def do_POST(self):
        p = self.path.split("?")[0]
        if self.headers.get("X-Requested-With") != "opus":  # CSRF guard on top of SameSite=Strict
            return self._send(403, {"error": "bad request"})
        ip = self.client_address[0]
        try:
            body = self._json_body()
        except ValueError:
            return self._send(400, {"error": "invalid JSON"})

        if p == "/api/login":
            if not config.login_allowed(ip):
                return self._send(429, {"error": "too many attempts, wait a minute"})
            if config.check_password(str(body.get("password", ""))):
                config.login_ok(ip)
                sid = config.new_session()
                return self._send(200, {"ok": True}, extra={
                    "Set-Cookie": f"sid={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age={config.SESSION_TTL}"})
            config.login_failed(ip)
            return self._send(401, {"error": "wrong password"})

        if not self._authed():
            return self._send(401, {"error": "login required"})

        if p == "/api/logout":
            config.end_session(self._cookie())
            return self._send(200, {"ok": True}, extra={"Set-Cookie": "sid=; Max-Age=0; Path=/"})

        if p == "/api/keys":
            return self._keys(body)

        if p == "/api/settings":
            cfg = config.get()
            try:
                patch = {}
                if "filters" in body:
                    patch["filters"] = _clean_filters(body["filters"], cfg["filters"])
                if "warmup" in body:
                    w, cur = body["warmup"], cfg["warmup"]
                    patch["warmup"] = {
                        "enabled": bool(w.get("enabled", cur["enabled"])),
                        "top": max(0, min(5, int(w.get("top", cur["top"])))),
                        "bytes": max(65536, min(8_000_000, int(w.get("bytes", cur["bytes"])))),
                    }
                if "subtitles" in body:
                    patch["subtitles"] = _clean_subtitles(body["subtitles"], cfg["subtitles"])
                for k in ("torrentio_url", "stremio_url"):
                    if k in body:
                        v = str(body[k]).strip().rstrip("/")
                        if not re.fullmatch(r"https?://[^\s]+", v):
                            raise ValueError(f"{k} must be an http(s) URL")
                        patch[k] = v
            except (ValueError, TypeError) as e:
                return self._send(400, {"error": str(e)})
            config.update(patch)
            cache.clear()
            subtitles.clear_cache()
            _status_cache["v"] = None
            return self._send(200, {"ok": True})

        if p == "/api/password":
            if not config.check_password(str(body.get("current", ""))):
                return self._send(403, {"error": "current password is wrong"})
            new = str(body.get("new", ""))
            if len(new) < 10:
                return self._send(400, {"error": "new password must be at least 10 characters"})
            config.set_password(new)
            return self._send(200, {"ok": True})

        if p == "/api/cache/clear":
            cache.clear()
            return self._send(200, {"ok": True})

        return self._send(404, {"error": "not found"})

    def _keys(self, body):
        """Validate each submitted key against its API first; save only the ones that work."""
        results, patch = {}, {}
        cur = config.get()
        for name, label in (("tmdb_key", "TMDB"), ("tvdb_key", "TVDB")):
            if body.get(f"clear_{name}"):
                patch[name] = ""
                if name == "tvdb_key":
                    patch["tvdb_pin"] = ""
                results[name] = {"ok": True, "detail": "removed"}
                continue
            val = str(body.get(name, "")).strip()
            if not val:
                continue
            if len(val) > 2000 or re.search(r"\s", val):
                results[name] = {"ok": False, "detail": "that doesn't look like a key"}
                continue
            if name == "tmdb_key":
                ok, msg = Tmdb.validate(val)
            else:
                pin = str(body.get("tvdb_pin", cur["tvdb_pin"])).strip()
                ok, msg = Tvdb.validate(val, pin)
                if ok:
                    patch["tvdb_pin"] = pin
            results[name] = {"ok": ok, "detail": "saved, key works" if ok else msg}
            if ok:
                patch[name] = val
        if patch:
            config.update(patch)
            cache.clear()
            _status_cache["v"] = None
        return self._send(200, {"results": results})


def serve(handler, port):
    srv = ThreadingHTTPServer(("0.0.0.0", port), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    config.load()
    serve(Addon, ADDON_PORT)
    serve(Dashboard, DASH_PORT)
    log(f"opus-bridge {VERSION}: addon on :{ADDON_PORT}, dashboard on :{DASH_PORT}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
