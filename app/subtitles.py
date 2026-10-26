"""Subtitles from a Stremio subtitle addon (OpenSubtitles v3 by default).

Two ways out of here, because Stremio clients differ in which they read:
  * the addon `subtitles` resource   -> GET /subtitles/<type>/<id>.json
  * a `subtitles` list on each stream -> added by streams.build()
"""
import config
from util import ApiError, TTLCache, http_json, log

_cache = TTLCache(max_items=2000)
CACHE_TTL = 6 * 3600
EMPTY_TTL = 300  # an empty answer is often the addon having a bad moment; look again soon
TIMEOUT = 8

# Groups of equivalent codes: 2-letter, ISO 639-2/B, 639-2/T and OpenSubtitles' own variants.
# The first 3-letter code is the one we show back in the dashboard.
_GROUPS = [
    ("en", "eng"), ("es", "spa"), ("fr", "fre", "fra"), ("de", "ger", "deu"), ("it", "ita"),
    ("pt", "por", "pob", "pt-br"), ("ru", "rus"), ("ja", "jpn"), ("ko", "kor"), ("zh", "chi", "zho", "zht", "zhc"),
    ("hi", "hin"), ("ar", "ara"), ("tr", "tur"), ("nl", "dut", "nld"), ("pl", "pol"), ("sv", "swe"),
    ("id", "ind"), ("vi", "vie"), ("th", "tha"), ("he", "heb"), ("el", "gre", "ell"), ("cs", "cze", "ces"),
    ("ro", "rum", "ron"), ("hu", "hun"), ("bn", "ben"), ("ta", "tam"), ("te", "tel"), ("mr", "mar"),
    ("ur", "urd"), ("da", "dan"), ("fi", "fin"), ("no", "nor"), ("uk", "ukr"), ("fa", "per", "fas"),
]
_INDEX = {code: grp for grp in _GROUPS for code in grp}


def group_of(code):
    """All codes equivalent to `code` (lowercased); an unknown code only matches itself."""
    code = str(code).strip().lower()
    return _INDEX.get(code, (code,))


def canonical(code):
    """3-letter form shown in settings: 'en' -> 'eng'. Unknown codes are returned unchanged."""
    grp = group_of(code)
    return grp[1] if len(grp) > 1 else grp[0]


def clean_languages(value):
    """Accepts 'eng, es' or ['eng', 'es']; returns canonical, de-duplicated codes. Raises ValueError."""
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list):
        raise ValueError("languages must be a comma separated list such as: eng, spa")
    out = []
    for raw in items:
        c = str(raw).strip().lower()
        if not c:
            continue
        if not (2 <= len(c) <= 5) or not all(ch.isalpha() or ch == "-" for ch in c):
            raise ValueError(f"'{c}' is not a language code (use codes like eng, spa, fre, hin)")
        c = canonical(c)
        if c not in out:
            out.append(c)
    if len(out) > 8:
        raise ValueError("choose at most 8 languages")
    return out


def _pick(rows, languages, per_language):
    """Keep `per_language` subtitles for each wanted language, in the order the languages were listed."""
    out, seen = [], set()
    for want in languages:
        grp = set(group_of(want))
        n = 0
        for s in rows:
            if not isinstance(s, dict):
                continue
            url = str(s.get("url") or "")
            lang = str(s.get("lang") or "").lower()
            if lang not in grp or not url.startswith(("http://", "https://")) or len(url) > 2000 or url in seen:
                continue
            seen.add(url)
            sub = {"id": str(s.get("id") or url)[:200], "url": url, "lang": canonical(lang)}
            two = group_of(lang)[0]
            if len(two) == 2:
                sub["lang_code"] = two  # Gelato reads the 2-letter code straight from here
            # Gelato ranks subtitles by how well their title matches the release, and shows it in the picker.
            title = str(s.get("subtitleFileName") or s.get("movieReleaseName") or "").strip()
            if title:
                sub["title"] = title[:200]
            out.append(sub)
            n += 1
            if n >= per_language:
                break
    return out


def for_sid(kind, sid):
    """Subtitles for a Stremio id ('tt0133093' or 'tt0944947:1:2'). Never raises: returns [] on any problem."""
    cfg = config.get().get("subtitles") or {}
    if not cfg.get("enabled") or not cfg.get("languages"):
        return []
    langs, per = list(cfg["languages"]), int(cfg.get("per_language", 2))
    base = str(cfg.get("source_url", "")).rstrip("/")
    key = (base, kind, sid, tuple(langs), per)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    try:
        data = http_json(f"{base}/subtitles/{kind}/{sid}.json", timeout=TIMEOUT)
    except ApiError as e:
        log("subtitles lookup failed:", sid, e)
        return []
    rows = data.get("subtitles") if isinstance(data, dict) else None
    subs = _pick(rows if isinstance(rows, list) else [], langs, per)
    _cache.set(key, subs, CACHE_TTL if subs else EMPTY_TTL)
    log(f"subtitles {kind}/{sid}: {len(rows) if isinstance(rows, list) else 0} offered, {len(subs)} kept for {','.join(langs)}")
    return subs


def for_request(kind, raw_id):
    """Same, for an addon request id ('tmdb:603', 'tt0133093', 'tmdb:1399:1:2'). Needs the IMDb id."""
    import catalog  # local import: catalog also imports config
    idkind, ident, season, episode = catalog.split_id(raw_id)
    if idkind is None:
        return []
    try:
        imdb = catalog.imdb_for(kind, idkind, ident)
    except ApiError:
        return []
    if not imdb:
        return []
    if kind == "series":
        if season is None or episode is None:
            return []
        return for_sid(kind, f"{imdb}:{season}:{episode}")
    return for_sid(kind, imdb)


def clear_cache():
    _cache.clear()
