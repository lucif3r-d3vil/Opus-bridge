"""Small shared helpers: HTTP client, TTL cache, logging."""
import json
import threading
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; opus-bridge)"


class ApiError(Exception):
    """Upstream API failure. The message never contains URLs or keys."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def http_request(url, headers=None, data=None, method=None, timeout=20):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        raise ApiError(e.code, f"HTTP {e.code}") from None
    except Exception as e:  # URLError, timeouts, DNS ... never echo the URL
        raise ApiError(0, type(e).__name__) from None


def http_json(url, headers=None, data=None, method=None, timeout=20):
    _, body = http_request(url, headers, data, method, timeout)
    try:
        return json.loads(body)
    except ValueError:
        raise ApiError(0, "invalid JSON") from None


class TTLCache:
    def __init__(self, max_items=4000):
        self._d = {}
        self._lock = threading.Lock()
        self._max = max_items

    def get(self, key):
        with self._lock:
            hit = self._d.get(key)
            if not hit:
                return None
            exp, val = hit
            if exp < time.time():
                self._d.pop(key, None)
                return None
            return val

    def set(self, key, val, ttl):
        with self._lock:
            if len(self._d) >= self._max:
                now = time.time()
                for k in [k for k, (e, _) in self._d.items() if e < now]:
                    self._d.pop(k, None)
                if len(self._d) >= self._max:
                    self._d.clear()
            self._d[key] = (time.time() + ttl, val)

    def clear(self):
        with self._lock:
            self._d.clear()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)
