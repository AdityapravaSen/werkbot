# werkbot: Progress Log

What's done, what broke, and why things are the way they are.
The full plan lives in [`PLAN.md`](PLAN.md).

---

## Status

| Phase | What | Status |
|---|---|---|
| 0 | Project setup | ✅ Done |
| 1 | Scrapers | 🟡 In progress (4 of 14 sources, Group A done) |
| 2 | Deduplication | ⬜ Not started |
| 3 | Filtering (language, location, role) | ⬜ Not started |
| 4 | Fit scoring | ⬜ Not started |
| 5 | Resume + cover letter tailoring (LaTeX) | ⬜ Not started |
| 6 | Delivery + daily schedule | ⬜ Not started |

**Tests:** 24 passing · **Branch:** `phase-1-scrapers`

---

## Sources

| # | Source | Group | Status | Notes |
|---|---|---|---|---|
| 1 | Arbeitnow | A (API) | ✅ Working | Rate-limits at ~1 req/s, so we use a 3s delay |
| 2 | Arbeitsagentur | A (API) | ✅ Working | ~189 results for the default search |
| 3 | Ramstein AF (softgarden) | A (feed) | ✅ Working | No category in feed; filtered by city + title keywords |
| 4 | WeAreDevelopers | A (Markdown list) | ✅ Working | ~204 results, 24 per page; no description in the list |
| 5 | connecticum | B (HTML) | ⬜ | |
| 6 | e-fellows | B (HTML) | ⬜ | |
| 7 | Fraunhofer | B (HTML) | ⬜ | |
| 8 | germantechjobs | B (HTML) | ⬜ | |
| 9 | workingstudentjobs | B (HTML) | ⬜ | |
| 10 | foorilla | C (JavaScript) | ⬜ Disabled | Hidden endpoint not found yet |
| 11 | DFKI | C (JavaScript) | ⬜ Disabled | Hidden endpoint not found yet |
| 12–13 | MPI-INF (2 pages) | D (page watch) | ⬜ | |
| 14 | LinkedIn (2 searches) | E (jobspy) | ⬜ | |

---

## Changelog

### 2026-10-16
- **softgarden feed adapter** (`adapters/softgarden.py`): reads any softgarden career site's `jobs.feed.json`. Used for Ramstein AF, filtered by city and title keywords.
- **WeAreDevelopers adapter** (`adapters/wearedevelopers.py`): reads the Markdown version of the search (`jobs.md`) page by page until there is no "Next page" link.
- **`apply_url`** field on `Job`: the direct application link when a source provides one (WeAreDevelopers does).
- **`matches_keyword`** in `text.py`: short keywords like "IT" must be whole words ("IT-Support" yes, "Mitarbeiter" no).
- **CLI:** titles in the `--show` table are clickable links (Ctrl+click in the VS Code terminal).

### 2026-10-15
- **Arbeitsagentur adapter** (`adapters/arbeitsagentur.py`): search via the Jobsuche API, then one details request per job for the description. Reads the 2026 response format, with the old format as a fallback. Warns when results exist but can't be read.
- **Arbeitnow rate-limit handling:** 3s delay between pages, obeys `Retry-After`, up to 5 retries, and keeps partial results instead of failing.
- **Per-source settings:** any source can set `delay_s`, `max_pages` etc. in `sources.yaml`.
- **CLI:** quieter logs by default, `-v` for every HTTP request; a source that stops early shows as `partial` with the reason.

### 2026-10-14
- **Phase 0:** repo structure, `pyproject.toml` + `uv.lock`, SQLite schema (`jobs`, `job_sources`, `scores`, `applications`, `page_watch`), `sources.yaml` with all 14 sources, `werkbot` CLI (`sources`, `init-db`, `scrape`, `check-db`), GitHub Actions CI (lint + format + tests).
- **Arbeitnow adapter** (`adapters/arbeitnow.py`): pages through the public API (newest first) and keeps working-student jobs from the last 7 days.
- **Shared adapter base** (`adapters/base.py`): HTTP GET with automatic retries, a fake-HTTP hook for tests, and a registry of adapters.
- **`text.py`:** HTML → clean plain text for job descriptions.

---

## Problems & fixes

| Date | Problem | Cause | Fix |
|---|---|---|---|
| 10-16 | `Activate.ps1 cannot be loaded … running scripts is disabled` when opening a terminal | Windows blocks PowerShell scripts by default; VS Code tries to auto-activate `.venv` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (or ignore it: `uv run` doesn't need activation) |
| 10-16 | Ramstein feed has no IT/Engineering category | The JSON feed only has title, location, date, description | Filter on title keywords (configurable in `sources.yaml`) |
| 10-15 | Arbeitsagentur returned **0 jobs** even though the API reported 189 | The API renamed nearly every field (`stellenangebote` → `ergebnisliste`, `refnr` → `referenznummer`, `titel` → `stellenangebotsTitel`, `arbeitgeber` → `firma`). The public spec on GitHub is outdated. | Read the new names, keep the old ones as a fallback, warn when "jobs exist but none readable". Tests now use the real format. |
| 10-15 | `ImportError … partially initialized module (circular import)` | The new adapter file was missing or misnamed on disk. Python's message is misleading. | Saved the file with the correct name. Check with `Get-ChildItem src\werkbot\adapters`. |
| 10-15 | Fix seemed to have no effect (still 1s delay, `fail`) | The zip was extracted to the wrong place, so the old files were still in use | Switched to sending individual files with exact paths, no more zips |
| 10-14 | Arbeitnow **429 Too Many Requests** at page 18, and all 17 pages already fetched were lost | 1s between requests is too fast, and one exception threw away all results | 3s delay, obey `Retry-After`, keep partial results |
| 10-14 | `uv` not recognized in PowerShell | Terminal opened before uv was installed, so PATH wasn't updated | Restart the terminal (fully restart VS Code), or `$env:Path = "$HOME\.local\bin;$env:Path"` |
| 10-14 | `ImportError: Modest backend is deprecated` | selectolax 1.x removed the old HTML parser | Use `selectolax.lexbor.LexborHTMLParser` |
| 10-14 | Push rejected (non-fast-forward) | The GitHub repo was created with a LICENSE commit | `git pull … --allow-unrelated-histories -X theirs` |
| 10-14 | Wrong contributors shown on GitHub | The first commit had the wrong author email and a co-author line | Replaced the history with a single fresh commit and force-pushed |

---

## Decisions

| Decision | Why |
|---|---|
| Plain Python repo, **no n8n** | Most of the logic (scoring, LaTeX, filters) is code anyway; easier to test and version |
| **uv** for environments | Same versions everywhere (`uv.lock`); no global installs |
| **SQLite**, no vector DB | Hundreds to a few thousand jobs; numpy similarity is fast enough |
| **No RAG** | Resume + job description fit in one LLM prompt |
| APIs/feeds first, HTML second, browser last | APIs are faster and break less often |
| One adapter per source + `sources.yaml` | Adding or removing a site is a config change |
| Tests use a fake HTTP layer (`MockTransport`) | Fast, offline, never rate-limited; can simulate 429/503 |
| Adapters return partial results on errors | A blocked page shouldn't throw away everything already collected |
| Dropped datacareer, absolventa, academics.de | Not worth the effort |
| Arbeitsagentur: no `branche` (IT) filter | Not supported by the API; our own filters handle it in Phase 3 |
| WeAreDevelopers: read Markdown, not HTML cards | Plain text with labelled fields; far less likely to break on a redesign |
| Fit score, not "chance of getting hired" | No tool can predict hiring honestly |

**Open:** output format. The plan says Telegram; the later discussion favoured a static website + optional Telegram notification (no server). To decide before Phase 6.

---

## Known gaps

- **WeAreDevelopers has no description** in the list. Fit scoring needs one, so job pages will be fetched later (after dedup + filtering, to avoid ~200 extra requests).
- **The same job can appear twice within one source** (e.g. Ramstein posts one listing per open position). Phase 2 merges them.

## Next

1. Group B HTML adapters: connecticum, e-fellows, Fraunhofer, germantechjobs, workingstudentjobs
2. Group E: LinkedIn (python-jobspy)
3. Group D: MPI page watchers
4. Group C: foorilla, DFKI (find hidden endpoints)