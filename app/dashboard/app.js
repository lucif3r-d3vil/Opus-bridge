"use strict";
// No innerHTML anywhere: everything goes through el() so server data can't inject markup.
const app = document.getElementById("app");
let state = { tab: "pipeline", cfg: null, timer: null };

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") n.className = v;
    else if (k === "style") n.style.cssText = v; // CSSOM write: allowed by the strict CSP
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v === true) n.setAttribute(k, "");
    else if (v !== false && v != null) n.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
}

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Requested-With": "opus" },
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("login"); }
  if (!r.ok) throw new Error(data.error || "request failed");
  return data;
}

function clear() { clearInterval(state.timer); app.replaceChildren(); }

// ---------------------------------------------------------------- login
function showLogin(err) {
  clear();
  const pw = el("input", { type: "password", id: "pw", autocomplete: "current-password", required: true });
  const msg = el("p", { class: "msg bad" }, err || "");
  const form = el("form", { class: "login", onsubmit: async (e) => {
    e.preventDefault();
    try { await api("/api/login", { password: pw.value }); boot(); }
    catch (x) { msg.textContent = x.message; }
  } },
    el("h1", {}, "Opus Bridge"),
    el("p", { class: "muted" }, "Sign in to manage keys, filters and playback."),
    el("div", { class: "field" }, el("label", { for: "pw" }, "Admin password"), pw,
       el("span", { class: "hint" }, "On first run it is printed once in the container log: docker logs opus-bridge")),
    msg,
    el("div", { class: "actions" }, el("button", { class: "btn", type: "submit" }, "Sign in")));
  app.append(form);
  pw.focus();
}

// ---------------------------------------------------------------- shell
function shell(version) {
  const tabs = [["pipeline", "Pipeline"], ["settings", "Settings"], ["security", "Security"]];
  const nav = el("nav", { class: "tabs", role: "tablist" }, tabs.map(([id, label]) =>
    el("button", { role: "tab", "aria-selected": String(state.tab === id), onclick: () => { state.tab = id; render(); } }, label)));
  const body = el("div", { id: "body" });
  app.replaceChildren(
    el("header", { class: "top" },
      el("div", { class: "brand" }, el("h1", {}, "Opus Bridge"), el("span", { class: "muted small" }, "v" + version)),
      el("button", { class: "btn quiet", onclick: async () => { await api("/api/logout", {}); showLogin(); } }, "Sign out")),
    nav, body);
  return body;
}

// ---------------------------------------------------------------- pipeline tab
function node(name, st, detailFallback) {
  const cls = st == null ? "off" : st.ok === true ? "ok" : st.ok === false ? "bad" : "off";
  return el("div", { class: "node" },
    el("h3", {}, el("span", { class: "dot " + cls }), name),
    el("p", {}, st ? st.detail : detailFallback));
}

async function pipelineTab(body) {
  const [status, cfg] = await Promise.all([api("/api/status"), api("/api/config")]);
  const catalogsOk = status.tmdb.ok === true;
  const catSt = { ok: catalogsOk ? true : status.tmdb.ok,
    detail: "TMDB " + status.tmdb.detail + (status.tvdb.ok === null ? "" : "; TVDB " + status.tvdb.detail) };
  const link = (on) => el("span", { class: "link" + (on ? " live" : "") });
  const copyInput = el("input", { type: "text", readonly: true, value: cfg.addon_url, "aria-label": "Addon URL" });
  const copyMsg = el("span", { class: "msg ok" });

  body.replaceChildren(
    el("section", { class: "block" },
      el("h2", {}, "Pipeline"),
      el("p", { class: "muted lede" }, "Each hop a title passes through before it plays in Jellyfin."),
      el("div", { class: "pipe" },
        node("Catalogs", catSt), link(catalogsOk),
        node("Bridge", { ok: true, detail: "running" }), link(status.torrentio.ok === true),
        node("Torrentio", status.torrentio), link(status.stremio.ok === true),
        node("Stremio server", status.stremio)),
      !catalogsOk ? el("p", { class: "msg bad", style: "margin-top:14px" },
        "Catalogs are empty until a TMDB key is saved. Add it under Settings.") : null),
    el("section", { class: "block" },
      el("h2", {}, "Gelato addon URL"),
      el("p", { class: "muted" }, "Paste this into Gelato's addon field in Jellyfin, then press Test and Save."),
      el("div", { class: "copy-row" }, copyInput,
        el("button", { class: "btn quiet", onclick: async () => {
          copyInput.select();
          try { await navigator.clipboard.writeText(copyInput.value); copyMsg.textContent = "Copied"; }
          catch { copyMsg.textContent = "Press Ctrl+C to copy"; }
        } }, "Copy"), copyMsg)),
    el("section", { class: "block", id: "activity" }));
  await activity();
  state.timer = setInterval(() => { if (state.tab === "pipeline") activity(); }, 5000);
}

const DROP_LABELS = { resolution: "quality not allowed", seeders: "too few seeders", size: "size out of range",
  keyword: "blocked keyword", "no fileIdx": "no file index", "no usable file": "no usable file", "no infoHash": "no hash" };

async function activity() {
  const box = document.getElementById("activity");
  if (!box) return;
  const rows = await api("/api/activity");
  const head = [el("h2", {}, "Recent stream requests"),
    el("p", { class: "muted lede" }, "Shows what Torrentio returned for each title and how many torrents your filters kept.")];
  if (!rows.length) {
    box.replaceChildren(...head, el("div", { class: "empty" }, "Nothing yet. Open a title in Jellyfin and it will show up here."));
    return;
  }
  const list = el("ul", { class: "log" }, rows.map((r) => {
    const pct = r.raw ? Math.max(r.kept ? 4 : 100, Math.round((r.kept / r.raw) * 100)) : 0;
    const dropped = Object.entries(r.dropped || {}).filter(([, n]) => n > 0)
      .map(([k, n]) => `${DROP_LABELS[k] || k} \u00d7${n}`).join(", ");
    return el("li", {},
      el("span", { class: "t" }, r.t),
      el("div", {},
        el("div", { class: "id" }, r.id),
        el("div", { class: "funnel" },
          el("span", { class: "bar" }, el("i", { class: r.kept ? "" : "none", style: `width:${pct}%` })),
          el("span", { class: "n" }, `${r.kept} kept of ${r.raw}`)),
        r.pick ? el("div", { class: "why" }, "Top pick: " + r.pick) : null,
        r.kept && r.subs !== null && r.subs !== undefined
          ? el("div", { class: "why" }, r.subs ? `Subtitles offered: ${r.subs}` : "No subtitles found") : null,
        r.note ? el("div", { class: "why" }, el("b", {}, r.note)) : null,
        dropped ? el("div", { class: "why" }, "Dropped: " + dropped) : null));
  }));
  box.replaceChildren(...head, list);
}

// ---------------------------------------------------------------- settings tab
function field(label, input, hint) {
  return el("div", { class: "field" }, el("label", {}, label, input), hint ? el("span", { class: "hint" }, hint) : null);
}

async function settingsTab(body) {
  const cfg = await api("/api/config");
  const f = cfg.filters, w = cfg.warmup, sb = cfg.subtitles;

  // keys ---------------------------------------------------------
  const tmdb = el("input", { type: "password", autocomplete: "off", placeholder: cfg.tmdb_key.set ? "Saved " + cfg.tmdb_key.hint + ". Type to replace." : "API key or read access token" });
  const tvdb = el("input", { type: "password", autocomplete: "off", placeholder: cfg.tvdb_key.set ? "Saved " + cfg.tvdb_key.hint + ". Type to replace." : "Optional" });
  const pin = el("input", { type: "password", autocomplete: "off", placeholder: "Only if your key needs one" });
  const keyMsg = el("div", { class: "msg" });
  const keysForm = el("form", { onsubmit: async (e) => {
    e.preventDefault(); keyMsg.replaceChildren(); keyMsg.className = "msg";
    try {
      const { results } = await api("/api/keys", { tmdb_key: tmdb.value, tvdb_key: tvdb.value, tvdb_pin: pin.value });
      for (const [k, r] of Object.entries(results))
        keyMsg.append(el("div", { class: "msg " + (r.ok ? "ok" : "bad") }, (k === "tmdb_key" ? "TMDB: " : "TVDB: ") + r.detail));
      if (!Object.keys(results).length) keyMsg.append("Nothing to save. Enter a key first.");
      tmdb.value = tvdb.value = pin.value = "";
      if (Object.values(results).some((r) => r.ok)) setTimeout(() => render(), 1200);
    } catch (x) { keyMsg.className = "msg bad"; keyMsg.textContent = x.message; }
  } },
    field("TMDB key", tmdb, "Free at themoviedb.org under Settings, API. The v3 key or the read access token both work."),
    field("TVDB key", tvdb, "Optional. Adds titles TMDB's search misses, which helps with anime."),
    field("TVDB PIN", pin),
    el("div", { class: "actions" },
      el("button", { class: "btn", type: "submit" }, "Test and save"),
      cfg.tvdb_key.set ? el("button", { class: "btn danger", type: "button", onclick: async () => { await api("/api/keys", { clear_tvdb_key: true }); render(); } }, "Remove TVDB key") : null),
    keyMsg);

  // filters ------------------------------------------------------
  const num = (v, attrs) => el("input", Object.assign({ type: "number", value: v, step: "any" }, attrs));
  const inp = {
    seeders: num(f.min_seeders, { min: 0 }), mmin: num(f.movie_min_mb, { min: 0 }), mmax: num(f.movie_max_gb, { min: 0.1 }),
    smin: num(f.series_min_mb, { min: 0 }), smax: num(f.series_max_gb, { min: 0.1 }), max: num(f.max_streams, { min: 1, max: 50 }),
    kw: el("input", { type: "text", value: f.exclude_keywords }),
    fix: el("input", { type: "checkbox", checked: f.resolve_missing_fileidx }),
    warmOn: el("input", { type: "checkbox", checked: w.enabled }), warmTop: num(w.top, { min: 0, max: 5 }),
    tor: el("input", { type: "url", value: cfg.torrentio_url }), str: el("input", { type: "url", value: cfg.stremio_url }),
    subOn: el("input", { type: "checkbox", checked: sb.enabled }),
    subLang: el("input", { type: "text", value: sb.languages.join(", "), placeholder: "eng, spa" }),
    subPer: num(sb.per_language, { min: 1, max: 5 }),
    subUrl: el("input", { type: "url", value: sb.source_url }),
  };
  // resolution list: order = preference, unchecked = excluded
  let order = [...f.resolutions, ...cfg.resolution_options.filter((r) => !f.resolutions.includes(r))];
  const enabled = new Set(f.resolutions);
  const resUl = el("ul", { class: "reslist" });
  const drawRes = () => resUl.replaceChildren(...order.map((r, i) => el("li", {},
    el("input", { type: "checkbox", checked: enabled.has(r), "aria-label": "Allow " + r, onchange: (e) => e.target.checked ? enabled.add(r) : enabled.delete(r) }),
    el("span", { class: "name" }, r),
    el("button", { type: "button", disabled: i === 0, onclick: () => { [order[i - 1], order[i]] = [order[i], order[i - 1]]; drawRes(); } }, "Up"),
    el("button", { type: "button", disabled: i === order.length - 1, onclick: () => { [order[i + 1], order[i]] = [order[i], order[i + 1]]; drawRes(); } }, "Down"))));
  drawRes();

  const setMsg = el("div", { class: "msg" });
  const setForm = el("form", { onsubmit: async (e) => {
    e.preventDefault(); setMsg.className = "msg"; setMsg.textContent = "";
    try {
      await api("/api/settings", {
        filters: { min_seeders: inp.seeders.value, movie_min_mb: inp.mmin.value, movie_max_gb: inp.mmax.value,
          series_min_mb: inp.smin.value, series_max_gb: inp.smax.value, max_streams: inp.max.value,
          resolutions: order.filter((r) => enabled.has(r)), exclude_keywords: inp.kw.value,
          resolve_missing_fileidx: inp.fix.checked },
        warmup: { enabled: inp.warmOn.checked, top: inp.warmTop.value },
        subtitles: { enabled: inp.subOn.checked, languages: inp.subLang.value, per_language: inp.subPer.value,
          source_url: inp.subUrl.value },
        torrentio_url: inp.tor.value, stremio_url: inp.str.value });
      setMsg.className = "msg ok"; setMsg.textContent = "Saved. Applies to the next request. In Gelato, press Save on the addon again so it re-reads the manifest.";
    } catch (x) { setMsg.className = "msg bad"; setMsg.textContent = x.message; }
  } },
    el("div", { class: "field" }, el("span", { class: "lab" }, "Qualities"),
      el("span", { class: "hint" }, "Checked qualities are allowed. Higher in the list is preferred when sorting."), resUl),
    el("div", { class: "row" }, field("Minimum seeders", inp.seeders), field("Streams to offer", inp.max)),
    el("div", { class: "row" }, field("Movie size, min MB", inp.mmin), field("Movie size, max GB", inp.mmax)),
    el("div", { class: "row" }, field("Episode size, min MB", inp.smin), field("Season pack size, max GB", inp.smax)),
    field("Skip releases containing", inp.kw, "Comma separated. Matches whole words only."),
    el("label", { class: "check" }, inp.fix, "Look inside torrents Torrentio gives no file for (slower, finds more)"),
    el("div", { class: "field", style: "margin-top:22px" }, el("span", { class: "lab" }, "Warm-up"),
      el("span", { class: "hint" }, "When a title is opened, start fetching the best torrent so it plays sooner. Joins swarms for titles you may never watch.")),
    el("label", { class: "check" }, inp.warmOn, "Warm up the best streams"),
    el("div", { class: "row" }, field("How many to warm", inp.warmTop)),
    el("div", { class: "field", style: "margin-top:22px" }, el("span", { class: "lab" }, "Subtitles"),
      el("span", { class: "hint" }, "Fetched from a Stremio subtitle addon (OpenSubtitles by default) and attached to every stream. That addon learns the IMDb id of what you play.")),
    el("label", { class: "check" }, inp.subOn, "Offer subtitles"),
    el("div", { class: "row" },
      field("Languages", inp.subLang, "Codes such as eng, spa, fre, ger, hin. Listed first = preferred."),
      field("Per language", inp.subPer, "How many to offer for each language (1 to 5).")),
    field("Subtitle addon URL", inp.subUrl, "Any Stremio subtitle addon. Leave as is unless you run your own."),
    el("div", { class: "field", style: "margin-top:22px" }, el("span", { class: "lab" }, "Services")),
    field("Torrentio URL", inp.tor, "Add Torrentio options to the path if you use them, for example https://torrentio.strem.fun/providers=yts,eztv"),
    field("Stremio server URL", inp.str, "Where the bridge reaches stremio-server inside Docker."),
    el("div", { class: "actions" }, el("button", { class: "btn", type: "submit" }, "Save settings"), setMsg));

  body.replaceChildren(
    el("section", { class: "block" }, el("h2", {}, "Metadata keys"),
      el("p", { class: "muted lede" }, "Keys are checked against TMDB and TVDB before saving, stored on this server only, and never shown again."), keysForm),
    el("section", { class: "block" }, el("h2", {}, "Streams and filters"),
      el("p", { class: "muted lede" }, "Decide which torrents are offered to Jellyfin."), setForm));
}

// ---------------------------------------------------------------- security tab
function securityTab(body) {
  const cur = el("input", { type: "password", autocomplete: "current-password", required: true });
  const nw = el("input", { type: "password", autocomplete: "new-password", required: true, minlength: 10 });
  const msg = el("div", { class: "msg" });
  body.replaceChildren(el("section", { class: "block" }, el("h2", {}, "Admin password"),
    el("p", { class: "muted lede" }, "Used for this dashboard only. At least 10 characters."),
    el("form", { onsubmit: async (e) => {
      e.preventDefault(); msg.className = "msg";
      try { await api("/api/password", { current: cur.value, new: nw.value }); msg.className = "msg ok"; msg.textContent = "Password changed."; cur.value = nw.value = ""; }
      catch (x) { msg.className = "msg bad"; msg.textContent = x.message; }
    } },
      field("Current password", cur), field("New password", nw),
      el("div", { class: "actions" }, el("button", { class: "btn", type: "submit" }, "Change password"), msg))));
}

// ---------------------------------------------------------------- routing
let version = "";
async function render() {
  clearInterval(state.timer);
  const body = shell(version);
  try {
    if (state.tab === "pipeline") await pipelineTab(body);
    else if (state.tab === "settings") await settingsTab(body);
    else securityTab(body);
  } catch (x) { if (x.message !== "login") body.replaceChildren(el("p", { class: "msg bad" }, x.message)); }
}

async function boot() {
  const s = await api("/api/session").catch(() => ({ authed: false }));
  version = s.version || "";
  if (s.authed) render(); else showLogin();
}
boot();
