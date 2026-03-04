# Changelog

## v2.0 — Parallel Rewrite

Complete rewrite of `modrinth_finder.py` → `modrinth_finder_v2.py` focused on speed, reliability, and user control.

### Performance (73 mods, fabric loader, 16-thread CPU)

| Metric | v1 (`modrinth_finder.py`) | v2 (`modrinth_finder_v2.py`) | Improvement |
|---|---|---|---|
| **Total runtime** | ~208 s | **19–44 s** | **5–11× faster** |
| Search (Phase 1) | ~150 s (sequential) | **5–11 s** (parallel) | **14–30× faster** |
| Version fetch (Phase 2) | included in search | **5–9 s** (parallel) | — |
| Dep resolution (Phase 3) | included in search | **1–2 s** (bulk API) | — |
| Analysis | ~55 s (sequential) | **6–7 s** (parallel) | **8× faster** |
| Thread count | 1 (fully sequential) | `os.cpu_count()` (auto) | max hardware utilisation |
| API calls strategy | 1 at a time | 16 concurrent + retry | connection pool sized to workers |

### Accuracy

| Metric | v1 | v2 | Match |
|---|---|---|---|
| Best MC version | 1.21.1 | 1.21.1 | ✓ identical |
| Mods discovered | 91 | 91 | ✓ identical |
| Mod names | all 91 | all 91 | ✓ identical |
| Wrong-loader detection | TrueDarkness, Weather Storms | TrueDarkness, Weather Storms | ✓ identical |
| Alt-project resolution | Guard Villagers alt | Guard Villagers alt | ✓ identical |
| Compatible count | 70/70 | 70/70 | ✓ identical |

### New Features in v2

- **Integrated downloader** — no separate `download_mods.py` needed; use `-d mods` or interactive menu
- **Client/server split downloads** — `--split --bias client` separates jars by side
- **Phase timing breakdown** — shows per-phase duration (search, versions, deps, analysis)
- **Progress counters** — live `Searching... 45/73` and `Fetching versions... 30/71` progress
- **Worker count display** — shows `Workers: 16 threads (cpu_count=16)` at start
- **Retry adapter** — automatic retry on 429/5xx with exponential backoff (5 retries, 0.6s factor)
- **Connection pool** — HTTP pool sized to match worker count for maximum throughput
- **Bulk API endpoints** — uses `/projects?ids=[...]` and `/versions?ids=[...]` to batch requests
- **Thread-safe caches** — version and project data cached across all threads with locks

### Architecture Changes (v1 → v2)

| Component | v1 | v2 |
|---|---|---|
| Search | Sequential BFS queue; 1 mod at a time | Parallel ThreadPoolExecutor; all mods searched concurrently |
| Version fetch | Inline during search (sequential) | Separate parallel phase after search completes |
| Dep resolution | BFS queue, sequential per-dep API calls | BFS with bulk `/projects` + parallel version fetch per wave |
| Analysis | Sequential: 5 MC versions analysed one-by-one | Parallel: all 5 versions analysed concurrently |
| Alt-project search | Sequential per-mod within each version | Parallel per-mod within each version |
| Downloads | Separate script (`download_mods.py`, 4 workers) | Integrated, `os.cpu_count()` workers |
| Rate limit handling | None (crashes on 429) | `urllib3.Retry` with exponential backoff |
| HTTP pooling | Default (10 connections) | Sized to `os.cpu_count()` |
| Config fields | `mods`, `ignore`, `skip-results`, `manual_mods` | Same (backward compatible) |
| Output | Shows top 3 versions always | Shows only perfect version if one exists (cleaner) |

### Config Format

No changes — v2 reads the same `config.json` format as v1. Fully backward compatible.

### Files

| File | Purpose |
|---|---|
| `modrinth_finder_v2.py` | Main script (v2) — search, analyse, download |
| `modrinth_finder.py` | Original script (v1) — preserved for reference |
| `config.json` | Mod list and settings |
| `requirements.txt` | Python dependencies |
| `setup.bat` | Windows quick-start helper |
| `README.md` | Full documentation |
