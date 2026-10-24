"""Persistent settings, admin password and dashboard sessions."""
import copy
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from util import log

DATA_DIR = os.environ.get("DATA_DIR", "/data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

SECRET_KEYS = ("tmdb_key", "tvdb_key", "tvdb_pin")

DEFAULTS = {
    "tmdb_key": "",
    "tvdb_key": "",
    "tvdb_pin": "",
    "torrentio_url": "https://torrentio.strem.fun",
    "stremio_url": os.environ.get("STREMIO_URL", "http://stremio-server:11470"),
    "filters": {
        "min_seeders": 3,
        "movie_min_mb": 500,
        "movie_max_gb": 20,
        "series_min_mb": 100,
        "series_max_gb": 80,
        "max_streams": 10,
        "resolutions": ["1080p", "720p", "2160p", "480p"],
        "exclude_keywords": "cam,hdcam,telesync,hdts,screener,workprint,telecine",
        "resolve_missing_fileidx": True,
    },
    "warmup": {"enabled": True, "top": 1, "bytes": 262144},
    "admin": {"salt": "", "hash": ""},
}

RESOLUTIONS = ["2160p", "1080p", "720p", "480p", "other"]

_lock = threading.RLock()
_cfg = None


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


def _hash_pw(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def _save_locked():
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(_cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def load():
    global _cfg
    with _lock:
        stored = {}
        try:
            with open(CONFIG_PATH) as f:
                stored = json.load(f)
        except FileNotFoundError:
            pass
        except ValueError:
            log("config.json is corrupt, starting from defaults (old file kept as .bad)")
            os.replace(CONFIG_PATH, CONFIG_PATH + ".bad")
        _cfg = _merge(DEFAULTS, stored)
        # keys can also come from the environment (first boot convenience)
        for env, key in (("TMDB_API_KEY", "tmdb_key"), ("TVDB_API_KEY", "tvdb_key")):
            if not _cfg[key] and os.environ.get(env):
                _cfg[key] = os.environ[env].strip()
        if not _cfg["admin"]["hash"]:
            pw = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(12)
            generated = not os.environ.get("ADMIN_PASSWORD")
            set_password(pw, _locked=True)
            if generated:
                log("=" * 60)
                log("First run. Dashboard admin password (shown once):")
                log("    " + pw)
                log("Change it in the dashboard under Security.")
                log("=" * 60)
        _save_locked()


def get():
    with _lock:
        return copy.deepcopy(_cfg)


def update(patch):
    with _lock:
        merged = _merge(_cfg, patch)
        _cfg.clear()
        _cfg.update(merged)
        _save_locked()


def set_password(pw, _locked=False):
    with _lock:
        salt = secrets.token_hex(16)
        _cfg["admin"] = {"salt": salt, "hash": _hash_pw(pw, salt)}
        if not _locked:
            _save_locked()


def check_password(pw):
    with _lock:
        a = _cfg["admin"]
        return hmac.compare_digest(_hash_pw(pw, a["salt"]), a["hash"])


def masked():
    c = get()
    c.pop("admin", None)
    for k in SECRET_KEYS:
        v = c.get(k, "")
        c[k] = {"set": bool(v), "hint": ("…" + v[-4:]) if len(v) >= 8 else ("set" if v else "")}
    return c


# --- sessions and login throttling -------------------------------------------------

_sessions = {}
_fails = {}
SESSION_TTL = 12 * 3600


def new_session():
    tok = secrets.token_urlsafe(32)
    _sessions[tok] = time.time() + SESSION_TTL
    return tok


def valid_session(tok):
    exp = _sessions.get(tok or "")
    if not exp:
        return False
    if exp < time.time():
        _sessions.pop(tok, None)
        return False
    return True


def end_session(tok):
    _sessions.pop(tok or "", None)


def login_allowed(ip):
    n, until = _fails.get(ip, (0, 0))
    return not (n >= 5 and until > time.time())


def login_failed(ip):
    n, until = _fails.get(ip, (0, 0))
    n += 1
    _fails[ip] = (n, time.time() + 60 * min(n, 10))


def login_ok(ip):
    _fails.pop(ip, None)
