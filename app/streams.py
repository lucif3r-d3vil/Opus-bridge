"""Stream selection: Torrentio results -> playable stremio-server URLs, filtered by dashboard settings."""
import collections
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import catalog
import config
import subtitles
from util import ApiError, http_json, http_request, log

VIDEO_EXT = (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".webm", ".ts")
activity = collections.deque(maxlen=200)

_warmed = {}
_warm_slots = threading.Semaphore(3)
_sub_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="subs")


def _res_of(name):
    n = name.lower()
    if "4k" in n or "2160p" in n:
        return "2160p"
    for r in ("1080p", "720p", "480p"):
        if r in n:
            return r
    return "other"


def _size_mb(title):
    m = re.search(r"\U0001F4BE\s*([\d.]+)\s*(TB|GB|MB)", title)
    if not m:
        return None
    return float(m.group(1)) * {"MB": 1, "GB": 1024, "TB": 1024 * 1024}[m.group(2)]


def _seeders(title):
    m = re.search(r"\U0001F464\s*(\d+)", title)
    return int(m.group(1)) if m else 0


def _excluded(text, keywords):
    words = [w.strip().lower() for w in keywords.split(",") if w.strip()]
    if not words:
        return False
    return re.search(r"(?<![a-z0-9])(" + "|".join(re.escape(w) for w in words) + r")(?![a-z0-9])", text.lower()) is not None


def _stremio_files(stremio, info_hash):
    data = http_json(f"{stremio}/{info_hash}/create", headers={"Content-Type": "application/json"},
                     data=b"{}", method="POST", timeout=20)
    return data.get("files") or []


def _pick_file(files, kind, season, episode):
    vids = [(f.get("length", 0), i, f.get("name", "")) for i, f in enumerate(files)
            if f.get("name", "").lower().endswith(VIDEO_EXT)]
    if not vids:
        return None
    if kind == "movie":
        return max(vids)[1]
    pats = [rf"s0*{season}e0*{episode}(?!\d)", rf"(?<!\d)0*{season}x0*{episode}(?!\d)"]
    for _, i, name in vids:
        low = name.lower()
        if any(re.search(p, low) for p in pats):
            return i
    return None


def build(kind, raw_id):
    """Returns (streams, log_entry). Never raises for upstream problems: returns [] and logs why."""
    cfg = config.get()
    flt = cfg["filters"]
    entry = {"t": time.strftime("%H:%M:%S"), "id": raw_id, "kind": kind, "raw": 0, "kept": 0,
             "dropped": {}, "note": "", "pick": "", "subs": None}
    sub_future = None
    activity.appendleft(entry)
    try:
        idkind, ident, season, episode = catalog.split_id(raw_id)
        if idkind is None:
            entry["note"] = "unsupported id"
            return []
        imdb = catalog.imdb_for(kind, idkind, ident)
        if not imdb:
            entry["note"] = "no IMDb id for this title (Torrentio needs one)"
            return []
        sid = imdb
        if kind == "series":
            if season is None or episode is None:
                entry["note"] = "series stream request without season/episode"
                return []
            sid = f"{imdb}:{season}:{episode}"
        if cfg["subtitles"]["enabled"]:
            # Looked up while Torrentio is being asked, so subtitles add no waiting time.
            sub_future = _sub_pool.submit(subtitles.for_sid, kind, sid)
        base = cfg["torrentio_url"].rstrip("/")
        data = http_json(f"{base}/stream/{kind}/{sid}.json", timeout=25)
    except ApiError as e:
        entry["note"] = f"upstream error: {e}"
        log("stream lookup failed:", raw_id, e)
        return []

    rows = data.get("streams") or []
    entry["raw"] = len(rows)
    drop = collections.Counter()
    allowed = list(flt["resolutions"])
    min_mb = flt["movie_min_mb"] if kind == "movie" else flt["series_min_mb"]
    max_mb = (flt["movie_max_gb"] if kind == "movie" else flt["series_max_gb"]) * 1024
    good, pending = [], []
    for s in rows:
        h = s.get("infoHash")
        title = s.get("title", "")
        if not h:
            drop["no infoHash"] += 1
            continue
        res = _res_of(s.get("name", ""))
        if res not in allowed:
            drop["resolution"] += 1
            continue
        if _seeders(title) < flt["min_seeders"]:
            drop["seeders"] += 1
            continue
        size = _size_mb(title)
        if size is None or size < min_mb or size > max_mb:
            drop["size"] += 1
            continue
        fname = (s.get("behaviorHints") or {}).get("filename", "")
        if _excluded(title + " " + fname, flt["exclude_keywords"]):
            drop["keyword"] += 1
            continue
        cand = {"hash": h, "idx": s.get("fileIdx"), "res": res, "seeders": _seeders(title),
                "title": title, "name": s.get("name", "Torrent")}
        (good if cand["idx"] is not None else pending).append(cand)

    rank = {r: i for i, r in enumerate(allowed)}
    keyf = lambda c: (rank.get(c["res"], 99), -c["seeders"])
    good.sort(key=keyf)
    pending.sort(key=keyf)

    # Torrents Torrentio gave no file index for: ask stremio-server which file is the video.
    if flt["resolve_missing_fileidx"] and len(good) < 3 and pending:
        def resolve(c):
            try:
                idx = _pick_file(_stremio_files(cfg["stremio_url"].rstrip("/"), c["hash"]),
                                 kind, season, episode)
                return c, idx
            except ApiError:
                return c, None

        with ThreadPoolExecutor(max_workers=2) as ex:
            for c, idx in ex.map(resolve, pending[:2]):
                if idx is None:
                    drop["no usable file"] += 1
                else:
                    c["idx"] = idx
                    good.append(c)
        good.sort(key=keyf)
    else:
        drop["no fileIdx"] += len(pending)

    good = good[: int(flt["max_streams"])]
    subs = []
    if sub_future is not None:
        try:
            subs = sub_future.result(timeout=subtitles.TIMEOUT + 2)
        except Exception as e:  # subtitles must never break playback
            log("subtitles skipped:", type(e).__name__)
    out = []
    for c in good:
        s = {
            "name": "Opus\n" + c["res"],
            "title": c["title"],
            "url": f"{cfg['stremio_url'].rstrip('/')}/{c['hash']}/{c['idx']}",
            "behaviorHints": {"notWebReady": False},
        }
        if subs:
            s["subtitles"] = subs
        out.append(s)
    if sub_future is not None:
        entry["subs"] = len(subs)
    entry["kept"] = len(out)
    entry["dropped"] = dict(drop)
    if out:
        entry["pick"] = out[0]["title"].split("\n")[0][:90]
        warm(out, cfg["warmup"])
    elif not entry["note"]:
        entry["note"] = "nothing passed the filters" if rows else "Torrentio returned nothing"
    return out


def _warm_one(url, nbytes):
    if not _warm_slots.acquire(blocking=False):
        return
    try:
        _, _ = http_request(url, headers={"Range": f"bytes=0-{nbytes - 1}"}, timeout=60)
        log("warmed", url.rsplit("/", 2)[-2][:8])
    except ApiError as e:
        log("warm failed:", e)
    finally:
        _warm_slots.release()


def warm(streams, w):
    if not w.get("enabled"):
        return
    now = time.time()
    for s in streams[: int(w.get("top", 1))]:
        u = s["url"]
        if now - _warmed.get(u, 0) < 600:
            continue
        _warmed[u] = now
        threading.Thread(target=_warm_one, args=(u, int(w.get("bytes", 262144))), daemon=True).start()
