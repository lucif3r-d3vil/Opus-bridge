"""TMDB and TVDB API clients. Keys come from config and are never logged or echoed."""
import json
import threading
import time
from urllib.parse import urlencode

import config
from util import ApiError, TTLCache, http_json

cache = TTLCache()


class Tmdb:
    BASE = "https://api.themoviedb.org/3"

    @staticmethod
    def _auth(key):
        # v4 read-access tokens are JWTs and start with "eyJ"; v3 keys are 32 hex chars.
        if key.startswith("eyJ"):
            return {"Authorization": "Bearer " + key}, {}
        return {}, {"api_key": key}

    @classmethod
    def request(cls, key, path, params=None, timeout=15):
        headers, auth_params = cls._auth(key)
        q = dict(params or {})
        q.update(auth_params)
        return http_json(f"{cls.BASE}{path}?{urlencode(q)}", headers=headers, timeout=timeout)

    @classmethod
    def get(cls, path, ttl=3600, **params):
        key = config.get()["tmdb_key"]
        if not key:
            raise ApiError(401, "TMDB key not set")
        ck = ("tmdb", path, tuple(sorted(params.items())))
        hit = cache.get(ck)
        if hit is not None:
            return hit
        data = cls.request(key, path, params)
        cache.set(ck, data, ttl)
        return data

    @classmethod
    def validate(cls, key):
        try:
            cls.request(key, "/configuration", timeout=10)
            return True, "ok"
        except ApiError as e:
            if e.status in (401, 403):
                return False, "TMDB rejected this key"
            return False, "could not reach TMDB (" + str(e) + ")"


class Tvdb:
    BASE = "https://api4.thetvdb.com/v4"
    _lock = threading.Lock()
    _token = None  # (token, expires, fingerprint)

    @classmethod
    def _login(cls, key, pin):
        body = {"apikey": key}
        if pin:
            body["pin"] = pin
        data = http_json(
            cls.BASE + "/login",
            headers={"Content-Type": "application/json"},
            data=json.dumps(body).encode(),
            method="POST",
            timeout=15,
        )
        tok = (data.get("data") or {}).get("token")
        if not tok:
            raise ApiError(401, "TVDB login returned no token")
        return tok

    @classmethod
    def token(cls, key=None, pin=None, force=False):
        c = config.get()
        key = key if key is not None else c["tvdb_key"]
        pin = pin if pin is not None else c["tvdb_pin"]
        if not key:
            raise ApiError(401, "TVDB key not set")
        fp = hash((key, pin))
        with cls._lock:
            t = cls._token
            if t and not force and t[2] == fp and t[1] > time.time():
                return t[0]
            tok = cls._login(key, pin)
            cls._token = (tok, time.time() + 20 * 24 * 3600, fp)
            return tok

    @classmethod
    def validate(cls, key, pin=""):
        try:
            cls.token(key, pin, force=True)
            return True, "ok"
        except ApiError as e:
            if e.status in (400, 401, 403):
                return False, "TVDB rejected this key (a subscriber PIN may be required)"
            return False, "could not reach TVDB (" + str(e) + ")"

    @classmethod
    def get(cls, path, ttl=3600, **params):
        ck = ("tvdb", path, tuple(sorted(params.items())))
        hit = cache.get(ck)
        if hit is not None:
            return hit
        for attempt in (0, 1):
            tok = cls.token(force=bool(attempt))
            try:
                data = http_json(
                    f"{cls.BASE}{path}?{urlencode(params)}",
                    headers={"Authorization": "Bearer " + tok},
                    timeout=15,
                )
                cache.set(ck, data, ttl)
                return data
            except ApiError as e:
                if e.status != 401 or attempt:
                    raise
        raise ApiError(401, "TVDB auth failed")

    @classmethod
    def enabled(cls):
        return bool(config.get()["tvdb_key"])

    @classmethod
    def search(cls, query, kind):
        """kind: 'movie' or 'series'. Returns [{'name','year','imdb','tvdb_id'}]."""
        data = cls.get("/search", 1800, query=query, type=kind, limit=8)
        out = []
        for item in data.get("data") or []:
            imdb = None
            for r in item.get("remote_ids") or []:
                if str(r.get("sourceName", "")).upper().startswith("IMDB"):
                    imdb = r.get("id")
            if imdb and str(imdb).startswith("tt"):
                out.append({
                    "name": item.get("name") or item.get("title"),
                    "year": item.get("year"),
                    "imdb": imdb,
                    "tvdb_id": item.get("tvdb_id") or item.get("id"),
                })
        return out
