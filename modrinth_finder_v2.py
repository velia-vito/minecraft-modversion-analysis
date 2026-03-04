#!/usr/bin/env python3
"""Modrinth Mod Compatibility Finder v2 — optimised rewrite.

Parallel search + parallel version fetching + bulk API calls,
integrated downloader with client/server split.
Finds the best common Minecraft version for a mod list, resolves deps,
detects conflicts, discovers alt-projects, and optionally downloads jars.

Design: the user always has final control. The script presents information
clearly and lets the user decide what to do.
"""

import requests, json, sys, os, argparse, re, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── UTF-8 on Windows ──────────────────────────────────────────────────────────
if sys.platform == "win32":
    os.system("chcp 65001 > nul")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

API = "https://api.modrinth.com/v2"
SEP = "=" * 100
MAX_WORKERS = os.cpu_count() or 4
_LOG_LOCK = Lock()


def log(msg="", **kw):
    with _LOG_LOCK:
        print(msg, flush=True, **kw)


# ── Version sorting ───────────────────────────────────────────────────────────

def _vk(v):
    """Parse a version string into a comparable tuple."""
    parts, num = [], ""
    for c in v:
        if c.isdigit():
            num += c
        else:
            if num:
                parts.append(int(num)); num = ""
            if c != ".":
                parts.append(c)
    if num:
        parts.append(int(num))
    return tuple(parts)


def vsort(vs):
    try:
        return sorted(vs, key=_vk, reverse=True)
    except Exception:
        return sorted(vs, reverse=True)


def strip_mc(vn, mcv):
    """Strip MC version prefix from a mod version string."""
    s = vn.strip()
    m = re.match(r'^(?:mc|fabric[- _]?)?\d+\.\d+(?:\.\d+)?[- _](.+)$', s, re.I)
    if m:
        return m.group(1)
    if s.lower().startswith('v') and len(s) > 1 and s[1:2].isdigit():
        return s[1:]
    return s


# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path):
    """Load and normalise config. Auto-sorts lists and writes back."""
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    loader = cfg.get("loader", "").lower().strip() or None
    mods = sorted([str(m).strip() for m in cfg.get("mods", []) if str(m).strip()], key=str.lower)
    ign = sorted([str(m).strip() for m in cfg.get("ignore", []) if str(m).strip()], key=str.lower)
    skip = sorted([str(m).strip() for m in cfg.get("skip-results", []) if str(m).strip()], key=str.lower)
    cfg["mods"], cfg["ignore"], cfg["skip-results"] = mods, ign, skip
    cfg.setdefault("manual_mods", [])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return mods, ign, loader, skip


# ── Modrinth API (with retry + caching) ──────────────────────────────────────

class MR:
    """Modrinth API client with retry, caching, and thread-safe caches."""

    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "ModFinder/4.0"
        # Auto-retry on rate limit (429) and server errors
        retry = Retry(total=5, backoff_factor=0.6,
                      status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["GET"])
        adapter = HTTPAdapter(max_retries=retry,
                              pool_connections=MAX_WORKERS,
                              pool_maxsize=MAX_WORKERS)
        self.s.mount("https://", adapter)
        self.s.mount("http://", adapter)
        self._ver_cache = {}   # version_id -> version data
        self._proj_cache = {}  # project_id -> project data
        self._lock = Lock()

    # ── slug normalisation ────────────────────────────────────────────────

    @staticmethod
    def slug_norm(s):
        return re.sub(r'[^a-z0-9-]', '', s.lower().replace(' ', '-'))

    # ── search ────────────────────────────────────────────────────────────

    def _best_hit(self, hits, sl, nl):
        """Score hits by match quality. Returns (hit, match_type) or (None, None)."""
        for h in hits:
            if self.slug_norm(h.get("slug", "")) == sl:
                return h, "exact"
        for h in hits:
            if h.get("title", "").lower() == nl:
                return h, "exact"
        for h in hits:
            if self.slug_norm(h.get("title", "")) == sl:
                return h, "exact"
        for h in hits:
            if h.get("title", "").lower().startswith(nl):
                return h, "prefix"
        for h in hits:
            if nl in h.get("title", "").lower():
                return h, "contains"
        return None, None

    def search(self, name, loader=None, skip=None):
        """Multi-tier search with wrong-loader detection.
        Returns (hit_dict, match_type_str) or (None, None).
        """
        skip_set = {s.lower() for s in (skip or [])}
        sl = self.slug_norm(name)
        nl = name.lower()

        def fetch(facets=None):
            params = {"query": name, "limit": 10}
            if facets:
                params["facets"] = facets
            try:
                r = self.s.get(f"{API}/search", params=params, timeout=15)
                r.raise_for_status()
                return [h for h in r.json().get("hits", [])
                        if h.get("title", "").lower() not in skip_set]
            except Exception:
                return []

        # Always search with loader filter first
        filtered = fetch(f'[["categories:{loader}"]]') if loader else fetch()
        fh, fm = self._best_hit(filtered, sl, nl) if filtered else (None, None)

        # If we got an exact/prefix match with the loader, use it
        if fh and fm in ("exact", "prefix"):
            return fh, fm

        # Search without loader filter to detect wrong-loader mods
        if loader:
            unfiltered = fetch()
            uh, um = self._best_hit(unfiltered, sl, nl) if unfiltered else (None, None)

            # The real mod exists but not for this loader
            if uh and um in ("exact", "prefix"):
                if fh and fm == um:
                    return fh, fm  # loader-compatible match of same quality
                return uh, "wrong-loader"

            # Filtered had a contains/fallback, unfiltered has exact — wrong loader
            if uh and um == "exact" and (not fh or fm not in ("exact", "prefix")):
                return uh, "wrong-loader"

        # Use filtered result if we had one
        if fh:
            return fh, fm

        # Last resort: best from filtered hits
        if filtered:
            return filtered[0], "fallback"

        return None, None

    # ── versions / single version / project ───────────────────────────────

    def versions(self, pid, loader=None):
        """Fetch all versions for a project, optionally filtered by loader."""
        try:
            r = self.s.get(f"{API}/project/{pid}/version", timeout=15)
            r.raise_for_status()
        except Exception:
            return []
        out = []
        for v in r.json():
            if loader and loader.lower() not in [l.lower() for l in v.get("loaders", [])]:
                continue
            with self._lock:
                self._ver_cache[v["id"]] = v
            out.append(v)
        return out

    def ver(self, vid):
        """Get a single version by ID (cached)."""
        with self._lock:
            if vid in self._ver_cache:
                return self._ver_cache[vid]
        try:
            r = self.s.get(f"{API}/version/{vid}", timeout=15)
            r.raise_for_status()
            v = r.json()
            with self._lock:
                self._ver_cache[vid] = v
            return v
        except Exception:
            return None

    def proj(self, pid):
        """Get project metadata by ID (cached)."""
        with self._lock:
            if pid in self._proj_cache:
                return self._proj_cache[pid]
        try:
            r = self.s.get(f"{API}/project/{pid}", timeout=15)
            r.raise_for_status()
            p = r.json()
            with self._lock:
                self._proj_cache[pid] = p
            return p
        except Exception:
            return None

    def proj_by_slug(self, slug):
        """Fetch a project by slug, returns None on 404."""
        try:
            r = self.s.get(f"{API}/project/{slug}", timeout=10)
            if r.status_code != 200:
                return None
            p = r.json()
            with self._lock:
                self._proj_cache[p["id"]] = p
            return p
        except Exception:
            return None

    # ── bulk helpers (Modrinth supports multi-ID endpoints) ───────────────

    def bulk_versions(self, version_ids):
        """Fetch multiple versions in one call."""
        need = []
        with self._lock:
            for vid in version_ids:
                if vid not in self._ver_cache:
                    need.append(vid)
        if not need:
            with self._lock:
                return {vid: self._ver_cache[vid] for vid in version_ids if vid in self._ver_cache}
        try:
            r = self.s.get(f"{API}/versions", params={"ids": json.dumps(need)}, timeout=20)
            r.raise_for_status()
            with self._lock:
                for v in r.json():
                    self._ver_cache[v["id"]] = v
        except Exception:
            pass
        with self._lock:
            return {vid: self._ver_cache[vid] for vid in version_ids if vid in self._ver_cache}

    def bulk_projects(self, project_ids):
        """Fetch multiple projects in one call."""
        need = []
        with self._lock:
            for pid in project_ids:
                if pid not in self._proj_cache:
                    need.append(pid)
        if not need:
            with self._lock:
                return {pid: self._proj_cache[pid] for pid in project_ids if pid in self._proj_cache}
        try:
            r = self.s.get(f"{API}/projects", params={"ids": json.dumps(need)}, timeout=20)
            r.raise_for_status()
            with self._lock:
                for p in r.json():
                    self._proj_cache[p["id"]] = p
        except Exception:
            pass
        with self._lock:
            return {pid: self._proj_cache[pid] for pid in project_ids if pid in self._proj_cache}


# ── Discovery ─────────────────────────────────────────────────────────────────

def discover(api, names, ign_set, loader, skip=None):
    """Search Modrinth for each mod name, fetch versions, discover deps.

    Phase 1: Parallel search for user mods (fast, retry adapter handles rate limits)
    Phase 2: Parallel project + version fetching
    Phase 3: BFS dep resolution with bulk project fetches + parallel version fetches

    Returns (info, not_found, deps) where:
      info  = {title: {gv, vd, slug, pid, url, dep, par, ign, client_side, server_side}}
      not_found = [name, ...]
      deps  = {parent_title: [dep_title, ...]}
    """
    info = {}
    deps = defaultdict(list)
    nf = []
    seen_titles = set()
    seen_pids = set()

    log(f"\n{SEP}")
    log(f"STAGE 1: SEARCH — Parallel search for {len(names)} mod names")
    log(f"{SEP}\n")
    # ── Phase 1: Parallel search ({MAX_WORKERS} threads) ─────────────────
    t_ph1 = time.time()
    found = []  # (name, hit, match_type)
    search_results = {}

    def _search_one(name):
        return name, api.search(name, loader, skip)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(_search_one, n): n for n in names}
        for fut in as_completed(futs):
            name, result = fut.result()
            search_results[name] = result
            log(f"\r  Searching... {len(search_results)}/{len(names)}", end="")
    log()

    # Process results in config order
    for name in names:
        h, mt = search_results[name]
        if not h:
            log(f"  ✗ \"{name}\" — not found")
            nf.append(name)
            continue
        if mt == "wrong-loader":
            pid = h.get("project_id") or h["slug"]
            proj_data = api.proj(pid)
            sl = proj_data.get("slug", h["slug"]) if proj_data else h["slug"]
            url = f"https://modrinth.com/mod/{sl}"
            log(f"  ✗ \"{name}\" — exists but NOT available for {loader}")
            log(f"                   {h['title']}")
            log(f"                   {url}")
            nf.append(name)
            continue
        found.append((name, h, mt))
    t_ph1_end = time.time() - t_ph1
    log(f"\n✓ STAGE 1 COMPLETE: {t_ph1_end:.1f}s  ({len(found)} found, {len(nf)} not found)\n")

    # ── Phase 2: Parallel project + version fetching ──────────────────────
    log(f"{SEP}")
    log(f"STAGE 2: VERSIONS — Fetching project metadata and versions")
    log(f"{SEP}\n")
    def _fetch_mod_data(name, hit, match_type):
        pid = hit.get("project_id") or hit["slug"]
        proj_data = api.proj(pid)
        sl = proj_data.get("slug", hit["slug"]) if proj_data else hit["slug"]
        cs = proj_data.get("client_side", "unknown") if proj_data else "unknown"
        ss = proj_data.get("server_side", "unknown") if proj_data else "unknown"
        vd = api.versions(pid, loader)
        return name, hit, match_type, pid, sl, vd, cs, ss

    t_ph2 = time.time()
    mod_data = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(_fetch_mod_data, n, h, m): n for n, h, m in found}
        for fut in as_completed(futs):
            mod_data.append(fut.result())
            log(f"\r  Fetching versions... {len(mod_data)}/{len(found)}", end="")
    log()

    # Sort results in config order for consistent output
    name_order = {n: i for i, n in enumerate(names)}
    mod_data.sort(key=lambda x: name_order.get(x[0], 999))

    dep_queue = []  # (dep_project_id, parent_title)
    for name, hit, match_type, pid, sl, vd, cs, ss in mod_data:
        t = hit["title"]
        if t in seen_titles:
            continue
        seen_titles.add(t)
        seen_pids.add(pid)
        url = f"https://modrinth.com/mod/{sl}"
        gv = set()
        for v in vd:
            gv.update(v.get("game_versions", []))
        icon = "⚠" if match_type in ("fallback", "contains") else "✓"
        log(f"  {icon} [MOD] \"{name}\"")
        log(f"                   {t}")
        log(f"                   {url}")
        info[t] = {
            "gv": gv, "vd": vd, "slug": sl, "pid": pid, "url": url,
            "dep": False, "par": None,
            "ign": name.lower() in ign_set or t.lower() in ign_set,
            "client_side": cs, "server_side": ss,
        }

        # Collect dependency project IDs from all versions
        dpids = set()
        for v in vd:
            for d in v.get("dependencies", []):
                if d.get("dependency_type") != "required":
                    continue
                dp = d.get("project_id")
                if not dp and d.get("version_id"):
                    vi = api.ver(d["version_id"])
                    if vi:
                        dp = vi.get("project_id")
                if dp and dp not in seen_pids:
                    dpids.add(dp)
        for dp in dpids:
            seen_pids.add(dp)
            dep_queue.append((dp, t))

    t_ph2_end = time.time() - t_ph2
    log(f"\n✓ STAGE 2 COMPLETE: {t_ph2_end:.1f}s  ({len(found)} mods, {len(dep_queue)} deps queued)\n")

    # ── Phase 3: BFS dependency resolution ────────────────────────────────
    log(f"{SEP}")
    log(f"STAGE 3: DEPENDENCIES — Resolving dependency tree (BFS)")
    log(f"{SEP}\n")
    t_ph3 = time.time()
    while dep_queue:
        # Bulk-fetch project metadata
        dep_pids = list({dp for dp, _ in dep_queue})
        proj_map = api.bulk_projects(dep_pids)
        # Fall back to individual for any missing
        for dp in dep_pids:
            if dp not in proj_map:
                p = api.proj(dp)
                if p:
                    proj_map[dp] = p

        # Build wave of new deps to process
        wave = []
        parent_map = {}  # title -> parent_title
        for dp, parent_title in dep_queue:
            p = proj_map.get(dp)
            if not p:
                continue
            t = p["title"]
            if t in seen_titles:
                continue
            seen_titles.add(t)
            wave.append((t, p, parent_title))
            parent_map[t] = parent_title

        dep_queue = []

        # Parallel version fetch for this wave
        def _fetch_dep(title, proj, parent_title):
            pid = proj["id"]
            sl = proj.get("slug", pid)
            cs = proj.get("client_side", "unknown")
            ss = proj.get("server_side", "unknown")
            vd = api.versions(pid, loader)
            return title, proj, parent_title, pid, sl, vd, cs, ss

        if wave:
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                futs = [pool.submit(_fetch_dep, *args) for args in wave]
                wave_results = [f.result() for f in as_completed(futs)]

            # Sort alphabetically for consistent output
            wave_results.sort(key=lambda x: x[0].lower())

            for t, proj, parent_title, pid, sl, vd, cs, ss in wave_results:
                url = f"https://modrinth.com/mod/{sl}"
                gv = set()
                for v in vd:
                    gv.update(v.get("game_versions", []))
                log(f"  ✓ [DEP] \"{t}\" (req by {parent_title})")
                log(f"                   {t}")
                log(f"                   {url}")
                info[t] = {
                    "gv": gv, "vd": vd, "slug": sl, "pid": pid, "url": url,
                    "dep": True, "par": parent_title,
                    "ign": t.lower() in ign_set,
                    "client_side": cs, "server_side": ss,
                }
                deps[parent_title].append(t)

                # Collect next-level deps
                dpids = set()
                for v in vd:
                    for d in v.get("dependencies", []):
                        if d.get("dependency_type") != "required":
                            continue
                        dp = d.get("project_id")
                        if not dp and d.get("version_id"):
                            vi = api.ver(d["version_id"])
                            if vi:
                                dp = vi.get("project_id")
                        if dp and dp not in seen_pids:
                            dpids.add(dp)
                for dp in dpids:
                    seen_pids.add(dp)
                    dep_queue.append((dp, t))

    t_ph3_end = time.time() - t_ph3
    log(f"\n✓ STAGE 3 COMPLETE: {t_ph3_end:.1f}s\n")
    return info, nf, deps


# ── Analysis ──────────────────────────────────────────────────────────────────

def analyze(api, info, mcv, loader=None, skip=None):
    """Build install plan for a specific MC version.

    Returns (plan, conflicts, missing, alts) where:
      plan = {title: {vn, vid, deps, [alt, alt_url, alt_slug]}}
      conflicts = [{dep, selected, required, requesters}]
      missing = [{mod, ver, dep, slug, dver}]
      alts = {original_title: {title, slug, url, pid}}
    """
    pid2t = {i["pid"]: t for t, i in info.items()}
    plan = {}

    # First pass: build plan from known mods
    for t, i in info.items():
        cs = [v for v in i["vd"] if mcv in v.get("game_versions", [])]
        if not cs:
            continue
        plan[t] = {
            "vn": cs[0].get("version_number", "?"),
            "vid": cs[0]["id"],
            "deps": cs[0].get("dependencies", []),
        }

    # Second pass: alt-project resolution for mods NOT in plan (parallel)
    alts = {}
    needs_alt = [(t, i) for t, i in info.items() if t not in plan and not i.get("ign")]

    def _resolve_alt(t, i):
        """Try to find an alt project for a mod missing support for this MC version."""
        slug = i["slug"]
        candidates = [f"{slug}-{mcv}", f"{slug}-{mcv.replace('.', '')}"]
        for alt_slug in candidates:
            p = api.proj_by_slug(alt_slug)
            if not p:
                continue
            alt_pid = p["id"]
            if alt_pid in pid2t:
                continue
            vd = api.versions(alt_pid, loader)
            cs = [v for v in vd if mcv in v.get("game_versions", [])]
            if not cs:
                continue
            return (t, p, cs[0], p.get("slug", alt_slug))

        # Fallback: search "{title} {mcversion}"
        try:
            params = {"query": f"{t} {mcv}", "limit": 5}
            if loader:
                params["facets"] = f'[["categories:{loader}"]]'
            r = api.s.get(f"{API}/search", params=params, timeout=10)
            r.raise_for_status()
            skip_set = {s.lower() for s in (skip or [])}
            first_word = t.lower().split()[0]
            hits = [
                h for h in r.json().get("hits", [])
                if h.get("title", "").lower() not in skip_set
                and h.get("project_id") not in pid2t
                and first_word in h.get("title", "").lower()
            ]
            for h in hits:
                alt_pid = h.get("project_id") or h["slug"]
                vd = api.versions(alt_pid, loader)
                cs = [v for v in vd if mcv in v.get("game_versions", [])]
                if not cs:
                    continue
                return (t, {"id": alt_pid, "title": h["title"], "slug": h["slug"]}, cs[0], h["slug"])
        except Exception:
            pass
        return (t, None, None, None)

    if needs_alt:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = [pool.submit(_resolve_alt, t, i) for t, i in needs_alt]
            for fut in as_completed(futs):
                t, proj, version, alt_slug_real = fut.result()
                if proj and version:
                    alt_pid = proj["id"]
                    alt_title = proj["title"]
                    alt_url = f"https://modrinth.com/mod/{alt_slug_real}"
                    plan[t] = {
                        "vn": version.get("version_number", "?"),
                        "vid": version["id"],
                        "deps": version.get("dependencies", []),
                        "alt": alt_title, "alt_url": alt_url, "alt_slug": alt_slug_real,
                    }
                    alts[t] = {"title": alt_title, "slug": alt_slug_real, "url": alt_url, "pid": alt_pid}
                    pid2t[alt_pid] = t

    # ── Ensure alt project dependencies are resolvable ──────────────────────
    # Collect all dep project IDs from plan items and pre-fetch their metadata
    # to prevent "XjY0RcQj" (raw ID) from appearing as missing dependencies
    dep_pids_to_resolve = set()
    for t, p in plan.items():
        for d in p.get("deps", []):
            dp = d.get("project_id")
            if dp and dp not in pid2t:
                dep_pids_to_resolve.add(dp)

    # Fetch and cache metadata for all unresolved deps
    if dep_pids_to_resolve:
        for dp in dep_pids_to_resolve:
            pr = api.proj(dp)  # This will be cached by api.proj
            if pr:
                pid2t[dp] = pr.get("title", dp)

    # ── Conflict detection ────────────────────────────────────────────────

    # Bulk-prefetch version IDs needed for conflict checks
    needed_vids = set()
    for t, p in plan.items():
        for d in p["deps"]:
            if d.get("dependency_type") != "required":
                continue
            vid = d.get("version_id")
            if vid:
                needed_vids.add(vid)
    if needed_vids:
        api.bulk_versions(list(needed_vids))

    dep_reqs = defaultdict(list)
    missing = []
    seen_m = set()

    for t, p in plan.items():
        for d in p["deps"]:
            if d.get("dependency_type") != "required":
                continue
            dp = d.get("project_id")
            dv = d.get("version_id")
            if not dp and dv:
                vi = api.ver(dv)
                if vi:
                    dp = vi.get("project_id")
            if not dp:
                continue
            dt = pid2t.get(dp)
            if dt is None:
                if dp not in seen_m:
                    seen_m.add(dp)
                    pr = api.proj(dp)
                    dn = pr["title"] if pr else dp
                    ds = pr.get("slug", "") if pr else ""
                    dvn = None
                    if dv:
                        vi = api.ver(dv)
                        if vi:
                            dvn = vi.get("version_number")
                    missing.append({"mod": t, "ver": p["vn"], "dep": dn, "slug": ds, "dver": dvn})
                continue
            if dt not in plan or not dv:
                continue
            if plan[dt]["vid"] != dv:
                ok = set()
                for v in info[t]["vd"]:
                    if mcv not in v.get("game_versions", []):
                        continue
                    for dd in v.get("dependencies", []):
                        if dd.get("project_id") == dp and dd.get("version_id"):
                            ok.add(dd["version_id"])
                if ok and plan[dt]["vid"] not in ok:
                    rv = []
                    for vid in sorted(ok):
                        vi = api.ver(vid)
                        rv.append(vi.get("version_number", vid) if vi else vid)
                    dep_reqs[dt].append({"requirer": t, "req_vers": rv})

    conflicts = []
    for dt, reqs in dep_reqs.items():
        selected = plan[dt]["vn"] if dt in plan else "?"
        requesters = [r["requirer"] for r in reqs]
        all_req = []
        for r in reqs:
            all_req.extend(r["req_vers"])
        conflicts.append({
            "dep": dt, "selected": selected,
            "required": sorted(set(all_req), key=str.lower),
            "requesters": requesters,
        })

    return plan, conflicts, missing, alts


# ── Display helpers ───────────────────────────────────────────────────────────

def ptable(rows):
    """Print a box-drawn table."""
    if not rows:
        return
    nc = len(rows[0])
    w = [max(len(str(r[i])) + 2 for r in rows) for i in range(nc)]
    def ln(l, m, r):
        return l + m.join("─" * x for x in w) + r
    def rw(c):
        return "│" + "│".join(f" {str(v):<{x-1}}" for v, x in zip(c, w)) + "│"
    log(ln("┌", "┬", "┐"))
    log(rw(rows[0]))
    log(ln("├", "┼", "┤"))
    for r in rows[1:]:
        log(rw(r))
    log(ln("└", "┴", "┘"))


def top_parent(t, info):
    """Trace dep chain to root parent. Returns the topmost parent mod's title, or empty if not found."""
    visited = set()
    cur = t
    while cur in info and info[cur].get("par") and cur not in visited:
        visited.add(cur)
        cur = info[cur]["par"]
    # Return the topmost parent (cur) if it's a real parent (different from t), otherwise return empty
    # This ensures we only show top parent link for mods involved in dependency chains
    return cur if cur != t and cur in info else ""


# ── Per-version display ──────────────────────────────────────────────────────

def show_version(api, info, deps, mcv, plan, conflicts, missing, active, alts=None):
    """Show install plan + conflicts + missing + not-latest for one MC version."""
    alts = alts or {}
    mains = {t: i for t, i in info.items() if not i["dep"]}

    # Determine which deps are relevant for THIS version's plan
    relevant_deps = set()
    pid2t = {i["pid"]: t for t, i in info.items()}
    for t, p in plan.items():
        for d in p.get("deps", []):
            if d.get("dependency_type") != "required":
                continue
            dp = d.get("project_id")
            if not dp and d.get("version_id"):
                vi = api.ver(d["version_id"])
                if vi:
                    dp = vi.get("project_id")
            if dp:
                dt = pid2t.get(dp)
                if dt:
                    relevant_deps.add(dt)

    ver_active = set()
    for t in active:
        if not info[t]["dep"]:
            ver_active.add(t)
        elif t in relevant_deps:
            ver_active.add(t)

    # Conflict index
    conf_idx = {}
    sorted_conflicts = sorted(conflicts, key=lambda x: x["dep"].lower())
    for i, c in enumerate(sorted_conflicts, 1):
        for req in c["requesters"]:
            conf_idx.setdefault(req, set()).add(i)

    sup = {t for t in ver_active if t in plan}
    miss = ver_active - sup

    # ── Header ──
    log(f"\n{'─' * 60}")
    log(f"  MC {mcv}  —  {len(sup)}/{len(ver_active)} compatible")
    log(f"{'─' * 60}")

    # ── Install plan ──
    log(f"\n  INSTALL PLAN:\n")
    rows = [("Mod", "Version", "Link")]
    for t in sorted(mains, key=str.lower):
        inf = mains[t]
        if t in plan and t in alts:
            alt = alts[t]
            iv = strip_mc(plan[t]["vn"], mcv)
            flag = ""
            if t in conf_idx:
                nums = " ".join(str(n) for n in sorted(conf_idx[t]))
                flag = f" ⚠ {nums}"
            rows.append((f"{t}{flag} → {alt['title']}", iv, alt["url"]))
        else:
            iv = strip_mc(plan[t]["vn"], mcv) if t in plan else "✗ N/A"
            flag = ""
            if t in conf_idx:
                nums = " ".join(str(n) for n in sorted(conf_idx[t]))
                flag = f" ⚠ {nums}"
            rows.append((f"{t}{flag}", iv, inf["url"]))
        for dt in sorted(deps.get(t, []), key=str.lower):
            di = info[dt]
            div = strip_mc(plan[dt]["vn"], mcv) if dt in plan else "✗ N/A"
            rows.append((f"  └─ {dt}", div, di["url"]))
    ptable(rows)

    # ── Conflicts + incompatible ──
    broken = set()
    for c in conflicts:
        broken.update(c["requesters"])

    req_detail = {}
    for c in sorted_conflicts:
        req_stripped = ", ".join(strip_mc(v, mcv) for v in c["required"])
        sel_stripped = strip_mc(c["selected"], mcv)
        detail = f"needs {c['dep']} {req_stripped} (selected: {sel_stripped})"
        for req in c["requesters"]:
            req_detail.setdefault(req, []).append(detail)

    if conflicts or miss:
        log(f"\n  ⚠ CONFLICTS ({len(broken) + len(miss - broken)}):\n")
        rows = [("#", "Mod", "Issue", "Link", "Top Parent Link")]
        n = 0
        for t in sorted(broken | miss, key=str.lower):
            n += 1
            inf = info[t]
            root = top_parent(t, info)
            rurl = info[root]["url"] if root else inf["url"]
            if t in req_detail:
                issue = "; ".join(req_detail[t])
            elif t not in sup:
                issue = f"no {mcv} support"
            else:
                issue = "dep conflict"
            rows.append((str(n), t, issue, inf["url"], rurl))
        ptable(rows)

    # ── Missing deps ──
    if missing:
        log(f"\n  ⚠ MISSING DEPENDENCIES:\n")
        rows = [("Required By", "Dependency", "Version", "Link")]
        for m in sorted(missing, key=lambda x: x["dep"].lower()):
            lnk = f"https://modrinth.com/mod/{m['slug']}" if m["slug"] else ""
            rows.append((m["mod"], m["dep"], strip_mc(m["dver"], mcv) if m["dver"] else "latest", lnk))
        ptable(rows)

    # ── Not latest ──
    downgraded = []
    for t, p in plan.items():
        if t in alts:
            continue
        if t not in info:
            continue
        mc_vers = [v for v in info[t]["vd"] if mcv in v.get("game_versions", [])]
        if len(mc_vers) < 2:
            continue
        newest = mc_vers[0].get("version_number", "?")
        if p["vn"] != newest:
            downgraded.append((t, strip_mc(p["vn"], mcv), strip_mc(newest, mcv), info[t]["url"]))
    if downgraded:
        log(f"\n  ℹ NOT LATEST:\n")
        rows = [("Mod", "Selected", f"Newest for {mcv}", "Link")]
        for t, iv, nv, url in sorted(downgraded, key=lambda x: x[0].lower()):
            rows.append((t, iv, nv, url))
        ptable(rows)


# ── Main display logic ────────────────────────────────────────────────────────

def show(api, info, deps, nf, loader, skip=None):
    """Compute coverage, analyse top versions, display results.

    Returns (best_version, best_plan, best_alts, all_analysis) or None.
    """
    active = {t for t, i in info.items() if not i["ign"] and not i["dep"]}
    ignored = sorted([t for t, i in info.items() if i["ign"]], key=str.lower)
    if nf:
        log(f"\nNot found ({loader or 'any'}): {', '.join(nf)}")
    if ignored:
        log(f"Ignored: {', '.join(ignored)}")

    # Coverage: which MC versions support which active mods
    cov = defaultdict(set)
    for t in active:
        for v in info[t]["gv"]:
            cov[v].add(t)
    cands = sorted(cov.items(), key=lambda x: (-len(x[1]), vsort({x[0]})))[:5]
    if not cands:
        log("No version data.")
        return None

    # Analyse top candidates (parallel — each MC version analysed concurrently)
    log(f"\n{SEP}")
    log(f"STAGE 4: ANALYSIS \u2014 Analyzing compatibility & searching for alternatives")
    log(f"{SEP}\n")
    t_ana = time.time()
    adj, ana = {}, {}
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(cands))) as pool:
        futs = {pool.submit(analyze, api, info, v, loader, skip): (v, sup) for v, sup in cands}
        ana_raw = {}
        for fut in as_completed(futs):
            v, sup = futs[fut]
            ana_raw[v] = (sup, fut.result())
    # Process in candidate order for consistent output
    for v, sup in cands:
        plan, conf, miss, alts = ana_raw[v][1]
        alt_resolved = {t for t in alts if t in plan}
        effective_sup = sup | alt_resolved
        broken = set()
        for c in conf:
            broken.update(c["requesters"])
        adj[v] = effective_sup - broken
        ana[v] = (plan, conf, miss, alts)
        notes = []
        if broken:
            notes.append(f"dep conflict → {', '.join(sorted(broken, key=str.lower))}")
        if alts:
            alt_strs = [f"{t} via {a['title']}" for t, a in sorted(alts.items(), key=lambda x: x[0].lower())]
            notes.append(f"alt found → {', '.join(alt_strs)}")
        if notes:
            log(f"  {v}: {'; '.join(notes)}")

    top = sorted(adj.items(), key=lambda x: (-len(x[1]), vsort({x[0]})))[:3]
    common = [v for v, s in adj.items() if s == active]
    best = vsort(common)[0] if common else (top[0][0] if top else None)

    if common:
        log(f"\n✓ Common version: {best}")
        # Only show the single perfect version — user doesn't need runner-ups
        plan, conflicts, missing, alts = ana[best]
        show_version(api, info, deps, best, plan, conflicts, missing, active, alts=alts)
    else:
        log(f"\n✗ No fully common version — showing top {len(top)} candidates")
        if top:
            log("\nBEST COVERAGE (max compat first, then latest MC):\n")
            rows = [("MC Version", "Coverage", "Incompatible")]
            for v, s in top:
                m = sorted(active - s, key=str.lower)
                rows.append((v, f"{len(s)}/{len(active)}", ", ".join(m) or "None"))
            ptable(rows)
        # Show per-version breakdown for top 3
        for v, s in top:
            plan, conflicts, missing, alts = ana[v]
            show_version(api, info, deps, v, plan, conflicts, missing, active, alts=alts)

    if not best:
        return None

    best_plan = ana[best][0]
    best_alts = ana[best][3]
    return best, best_plan, best_alts, ana


# ── Downloader ────────────────────────────────────────────────────────────────

def download_mods(api, plan, info, alts, mc_version, loader,
                  output_dir="mods", split=None, bias="client"):
    """Download mod jars for the install plan.

    split: None = single folder, "yes" = split with bias
    bias: "client" or "server" — which side gets ambiguous mods

    Client & server mods always go into both folders.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if split:
        client_dir = out / "client"
        server_dir = out / "server"
        client_dir.mkdir(exist_ok=True)
        server_dir.mkdir(exist_ok=True)

    dl_session = requests.Session()
    dl_session.headers["User-Agent"] = "ModDownloader/2.0"
    retry = Retry(total=3, backoff_factor=0.5,
                  status_forcelist=[429, 500, 502, 503, 504])
    dl_adapter = HTTPAdapter(max_retries=retry)
    dl_session.mount("https://", dl_adapter)

    stats = {"success": 0, "failed": 0, "skipped": 0}
    stats_lock = Lock()

    # Build download list — propagate parent side requirements to deps.
    # If a client mod requires a dep, that dep must also go to client
    # (and vice versa for server), regardless of the dep's own metadata.
    download_list = []

    # First pass: collect each mod's inherent sides
    mod_sides = {}  # title -> {"client": bool, "server": bool}
    for t in plan:
        cs = info[t].get("client_side", "unknown")
        ss = info[t].get("server_side", "unknown")
        mod_sides[t] = {
            "client": cs in ("required", "optional"),
            "server": ss in ("required", "optional"),
        }

    # Second pass: propagate parent sides to deps.
    # If parent is client-side, dep also needs to be on client.
    for t in plan:
        par = info[t].get("par")
        if par and par in mod_sides:
            if mod_sides[par]["client"]:
                mod_sides[t]["client"] = True
            if mod_sides[par]["server"]:
                mod_sides[t]["server"] = True

    for t, p in sorted(plan.items(), key=lambda x: x[0].lower()):
        slug = alts[t]["slug"] if t in alts else info[t]["slug"]
        vid = p["vid"]
        sides = mod_sides.get(t, {"client": False, "server": False})
        download_list.append({"name": t, "slug": slug, "vid": vid,
                              "is_client": sides["client"],
                              "is_server": sides["server"]})

    def _determine_dirs(is_client, is_server):
        if not split:
            return [out]
        if is_client and is_server:
            return [client_dir, server_dir]
        if is_client and not is_server:
            return [client_dir]
        if is_server and not is_client:
            return [server_dir]
        # Neither side determined — use bias
        return [server_dir] if bias == "server" else [client_dir]

    def _download_one(mod_info):
        name = mod_info["name"]
        vid = mod_info["vid"]
        try:
            v = api.ver(vid)
            if not v:
                with stats_lock:
                    stats["failed"] += 1
                return f"  ✗ {name}: version data not found"

            files = v.get("files", [])
            jar = None
            for f in files:
                if f.get("primary") or f["filename"].endswith(".jar"):
                    jar = f
                    if f.get("primary"):
                        break
            if not jar:
                with stats_lock:
                    stats["failed"] += 1
                return f"  ✗ {name}: no jar file"

            filename = jar["filename"]
            url = jar["url"]
            size_mb = jar.get("size", 0) / (1024 * 1024)
            dirs = _determine_dirs(mod_info["is_client"], mod_info["is_server"])

            all_exist = all((d / filename).exists() for d in dirs)
            if all_exist:
                with stats_lock:
                    stats["skipped"] += 1
                return f"  ⊘ {name}: already downloaded"

            data = dl_session.get(url, timeout=60).content
            dest_names = []
            for d in dirs:
                fp = d / filename
                if not fp.exists():
                    fp.write_bytes(data)
                dest_names.append(d.name)

            with stats_lock:
                stats["success"] += 1
            loc = " & ".join(dest_names) if split else str(out)
            return f"  ✓ {name}: {filename} ({size_mb:.1f} MB) [{loc}]"
        except Exception as e:
            with stats_lock:
                stats["failed"] += 1
            return f"  ✗ {name}: {e}"

    total = len(download_list)
    log(f"\nDownloading {total} mods (MC {mc_version}, {loader or 'any'})...")
    if split:
        log(f"  Split mode: bias={bias}  client={client_dir}  server={server_dir}")
    else:
        log(f"  Output: {out.resolve()}")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(_download_one, m): m for m in download_list}
        for fut in as_completed(futs):
            log(fut.result())

    log(f"\n  Summary: {stats['success']} downloaded, {stats['skipped']} skipped, {stats['failed']} failed")
    return stats


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Modrinth mod finder + downloader (v2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  %(prog)s config.json                      Search & analyse (interactive)
  %(prog)s config.json -o modslist.json     Export install plan
  %(prog)s config.json -d mods              Search & download to ./mods
  %(prog)s config.json -d mods --split --bias server
""")
    ap.add_argument("config", help="JSON config file")
    ap.add_argument("-o", "--output", help="Export install plan JSON")
    ap.add_argument("-d", "--download", metavar="DIR",
                    help="Download mods to this directory")
    ap.add_argument("--split", action="store_true",
                    help="Split downloads into client/server subdirs")
    ap.add_argument("--bias", choices=["client", "server"], default="client",
                    help="Which side gets ambiguous mods (default: client)")
    args = ap.parse_args()

    try:
        mods, ign, loader, skip = load_config(args.config)
    except Exception as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)
    if not mods:
        print("No mods in config.", file=sys.stderr)
        sys.exit(1)

    ign_set = {n.lower() for n in ign}
    log(f"\n{SEP}")
    log(f"SEARCHING {len(mods)} MODS — LOADER: {(loader or 'all').upper()}")
    if ign:
        log(f"IGNORING: {', '.join(ign)}")
    if skip:
        log(f"SKIPPING RESULTS: {', '.join(skip)}")
    log(SEP)

    t0 = time.time()
    api = MR()
    log(f"  Workers: {MAX_WORKERS} threads (cpu_count={os.cpu_count()})")
    info, nf, deps = discover(api, mods, ign_set, loader, skip)

    t_ana = time.time()
    result = show(api, info, deps, nf, loader, skip)
    t_ana_end = time.time() - t_ana
    
    elapsed = time.time() - t0
    log(f"\n✓ STAGE 4 COMPLETE: {t_ana_end:.1f}s")
    log(SEP)
    log(f"TOTAL TIME: {elapsed:.1f}s")
    log(SEP)
    log(f"\nCompleted in {elapsed:.1f}s")

    if not result:
        return

    best, plan, alts, ana = result

    # ── Helper to build export data ──
    def _build_export():
        mods_list = []
        for t in sorted(plan, key=str.lower):
            i = info[t]
            p = plan[t]
            slug = alts[t]["slug"] if t in alts else i["slug"]
            url = alts[t]["url"] if t in alts else i["url"]
            mods_list.append({
                "name": t, "version": p["vn"], "slug": slug,
                "url": url, "dependency": i["dep"],
                "parent": i["par"] or None,
            })
        return {"minecraft_version": best, "loader": loader, "mods": mods_list}

    # ── Export if -o given ──
    if args.output:
        export = _build_export()
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(export, f, indent=2, ensure_ascii=False)
            f.write("\n")
        log(f"\n✓ Exported {len(export['mods'])} mods to {args.output}")

    # ── Download if -d given ──
    if args.download:
        download_mods(api, plan, info, alts, best, loader,
                      output_dir=args.download,
                      split="yes" if args.split else None,
                      bias=args.bias)
        return

    # ── Interactive prompt (user decides what to do) ──
    if not args.output:
        log()
        log("What would you like to do?")
        log("  [d] Download mods")
        log("  [o] Export install plan as JSON")
        log("  [Enter] Done — exit")
        try:
            ans = input("\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if ans == "d":
            try:
                d = input("  Output directory [mods]: ").strip() or "mods"
                sp = input("  Split into client/server folders? [y/N]: ").strip().lower() == "y"
                b = "client"
                if sp:
                    b = input("  Ambiguous mods go to [client/server]: ").strip().lower()
                    if b not in ("client", "server"):
                        b = "client"
            except (EOFError, KeyboardInterrupt):
                return
            download_mods(api, plan, info, alts, best, loader,
                          output_dir=d,
                          split="yes" if sp else None,
                          bias=b)
        elif ans == "o":
            try:
                fn = input("  Filename [modslist.json]: ").strip() or "modslist.json"
            except (EOFError, KeyboardInterrupt):
                return
            export = _build_export()
            with open(fn, "w", encoding="utf-8") as f:
                json.dump(export, f, indent=2, ensure_ascii=False)
                f.write("\n")
            log(f"\n✓ Exported {len(export['mods'])} mods to {fn}")


if __name__ == "__main__":
    main()
