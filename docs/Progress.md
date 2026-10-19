# werkbot: Progress Log

What's done, what broke, and why things are the way they are.
The full plan lives in [`PLAN.md`](PLAN.md).

---

## Status

| Phase | What | Status |
|---|---|---|
| 0 | Project setup | ✅ Done |
| 1 | Scrapers | ✅ Done: all 14 sources built and live-tested (2026-10-16) |
| 2 | Deduplication + SQLite | ✅ Done (2026-10-19) |
| 3 | Filtering (language, location, role) | ⬜ Not started |
| 4 | Fit scoring | ⬜ Not started |
| 5 | Resume + cover letter tailoring (LaTeX) | ⬜ Not started |
| 6 | Delivery + daily schedule | ⬜ Not started |

**Tests:** 132 passing · **Phase 2 complete** (2026-10-19) · **Branch:** `phase-2-dedup`

---

## Sources

| # | Source | Group | Status | Notes |
|---|---|---|---|---|
Jobs = result of the first full run (2026-10-16), before dedup and filtering.

| # | Source | Group | Jobs | Notes |
|---|---|---|---|---|
| 1 | Arbeitnow | A (API) | 357 | Rate-limits at ~1 req/s (3s delay). Not sorted by date, so all pages are read |
| 2 | Arbeitsagentur | A (API) | 206 | One details request per job for the description (cap 300) |
| 3 | Ramstein AF (softgarden) | A (feed) | 9 of 75 | No category in feed; filtered by city + title keywords |
| 4 | WeAreDevelopers | A (Markdown list) | 204 | 24 per page; no description in the list |
| 5 | connecticum | B (HTML cards) | 97 | Follows `rel="next"`; mostly German ads |
| 6 | e-fellows | B (`__NEXT_DATA__` JSON) | 14 | Exact publish dates |
| 7 | Fraunhofer | B (HTML table) | 10 | No date in the list (detail page only) |
| 8 | germantechjobs | **A** (JSON API) | few of 1093 | Only 40 Internship + 1 Part-Time, and 38 English jobs in total |
| 9 | workingstudentjobs | B (Next.js page payload) | 14 | Gives `is_german_required` per job |
| 10 | foorilla | C (htmx HTML fragments) | 140 | Quick search + our location filter (60 outside Germany skipped) |
| 11 | DFKI | C (JSON feed) | ✅ ran | Two searches: Teilzeit + Wissenschaftl. Hilfskraft (count now logged) |
| 12–13 | MPI-INF (2 pages) | D (page watch) | unchanged | Snapshot in `output/watch/`; a change shows up as one "[Page changed]" item |
| 14 | LinkedIn (2 searches) | E (jobspy) | to confirm | Needs `uv sync --group dev --extra linkedin`; no descriptions by default |

---

## Changelog

### 2026-10-19 (Phase 2: deduplication)
- **Normalizers** (`dedup/normalize.py`): URL (https, no `www`, no tracking parameters, no trailing slash), title (gender tags, `Werkstudent*in`, `Working Student` = `Werkstudent` = `Studentische Hilfskraft` = `Student Assistant`, filler words), company (legal forms, aliases from `data/company_aliases.yaml`), city (postal codes, states, `Frankfurt am Main` = `Frankfurt`, `Munich` = `München`, remote words).
- **Four duplicate levels** (`dedup/match.py`): same URL → same source ID → same company + title + city → similar title at the same company and place, from a different board. "Similar" = same words in any order, a few extra words, or spelling variants (see `title_rule`).
- **Merge rules** (`dedup/merge.py`): main link from the best `kind` (employer > board > aggregator, new field in `sources.yaml`), longest description, earliest date, every link kept.
- **SQLite upsert** (`dedup/store.py`): the same four levels against the database, so a job is never stored twice across runs. Statuses: `new` → `active` → `expired` (14 days unseen) → `reposted` → `active`. Each source's full record is kept in `job_sources.data`.
- **Database:** new columns are added automatically to an existing `werkbot.db` (no need to delete it). New check: every job must have at least one source link.
- **CLI:** `scrape` now saves to the database (`--no-save` to skip). New `werkbot dedup [--dry-run]` merges the last raw results without scraping. The duplicate check runs after every save; a failure exits with code 1.
- **Tests with real duplicates:** Fraunhofer's own site vs connecticum (17 live listings from 2026-10-19) and Fraunhofer vs workingstudentjobs (the existing page fixtures).

### 2026-10-16 (Phase 1 wrap-up, from the full-run log)
- **Arbeitnow:** the API is **not sorted by date** (each page's oldest job swings between 10-09 and 10-14), so stopping at the first "too old" page never happens. `max_pages` raised 40 → 100 so every page is read; the 7-day filter still runs per job.
- **Arbeitsagentur:** description cap raised 200 → 300 (206 results, 6 had no description).
- **germantechjobs:** filter now uses the real values: `Part-Time`, `Internship`.
- **DFKI + LinkedIn** log how many jobs they found; every source writes a `RESULT …` line and the run ends with a `TOTAL …` line in the log file, so a log alone shows the full outcome.

### 2026-10-16 (first full run)
- **CLI:** UTF-8 output on Windows, a daily log file in `output/logs/` (full detail incl. every request), and a summary table (source / status / result) at the end of `scrape`.
- **Arbeitnow:** logs the oldest posting date on each page, to check the 7-day cutoff (first full run hit the 40-page cap with 338 matches).

### 2026-10-16 (Group C)
- **`scripts/probe_network.py`:** opens a page in headless Chromium (Playwright) and records every data request + response. Used to find the hidden endpoints of JavaScript sites.
- **DFKI adapter** (`adapters/dfki.py`): reads the Hireserve JSON feed (`ic_job_feeds.feed_engine`) the jobs page itself calls. Uses the English long title when available.
- **foorilla adapter** (`adapters/foorilla.py`): reads the htmx HTML fragments (`/hiring/jobs/?job_search=…&page=N`) and keeps jobs located in Germany.
- **Setup tests** now check that every source in `sources.yaml` has a built adapter.

### 2026-10-16 (Groups D + E)
- **LinkedIn adapter** (`adapters/linkedin.py`): runs `python-jobspy` (optional extra). A missing package or a LinkedIn block becomes a warning, not a crash. Tests use fake rows, so CI doesn't need jobspy.
- **Page watcher** (`adapters/page_watch.py`): extracts a page's main text (trafilatura), compares it with the last snapshot and reports added lines as one "[Page changed]" item. Used for the two MPI-INF pages.
- **Adapters register themselves:** `adapters/__init__.py` imports every module in the folder, so new adapters never require editing it.
- **CLI:** `scrape -s a -s b` runs several chosen sources; unknown names give a clear error.
- **Pre-commit hook** (`.githooks/pre-commit`, enabled with `git config core.hooksPath .githooks`): auto-formats staged Python files with ruff and blocks commits with lint errors.

### 2026-10-16 (Group B)
- **5 new adapters:** `connecticum.py`, `efellows.py`, `fraunhofer.py`, `germantechjobs.py`, `workingstudentjobs.py`.
- **`adapters/html.py`:** shared helpers to read HTML and the JSON that Next.js sites embed in pages.
- **`german_required`** field on `Job`, filled when a source states it (germantechjobs, workingstudentjobs).
- **`Adapter.warn()`** in the base class: one way for every adapter to report "partial / site changed".
- **Real-page test fixtures** in `tests/fixtures/`: trimmed copies of the actual sites' HTML.
- **Editor + git settings** (`.vscode/settings.json`, `.gitattributes`): always a final newline, LF line endings.

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
| 10-19 | The plan's fuzzy rule would merge "Werkstudent (m/w/d)" with "Werkstudent Data Science" | `token_set_ratio` is 100 whenever one title's words are a subset of the other's | Extra words only count as the same title if `token_sort_ratio >= 80` (it compares length) |
| 10-19 | A 1,500-job stress test merged "Werkstudent HR Backend Sales" with "Werkstudent Design Backend Sales" (score 93) | `token_set_ratio` stays high when the one differing word is short | Look at the differing words instead: they must be spelling variants ("Engineer"/"Engineering") |
| 10-19 | Same test: one company's "Software" and "Software Embedded" jobs merged | Fuzzy rules can't tell a shortened copy from a second, more specific job | Level 4 never merges two listings from the same board (only copies on another board) |
| 10-19 | One short title could chain two different jobs ("… Software" ~ "… Software Frontend" and ~ "… Software Backend") | Union of pairwise matches is transitive | Level 4 only joins two groups if every pair across them is similar |
| 10-19 | The same Fraunhofer job never matched across boards | Fraunhofer's site says "Fraunhofer SIT", connecticum says "Fraunhofer-Gesellschaft zur Förderung … e.V." | Company alias `fraunhofer` in `data/company_aliases.yaml` |
| 10-19 | Different foorilla jobs with the same title would merge | Adapters write "Unknown" when a board has no company | Unknown companies never match on company/title (levels 3–4), only on URL or ID |
| 10-16 | Arbeitnow always hit the 40-page cap | The API isn't sorted by date, so "a whole page too old → stop" never triggers | Read all pages (cap 100); filter age per job |
| 10-16 | Log file didn't show what LinkedIn/DFKI returned | Only some adapters logged counts; the summary table went to the screen only | Every source logs `RESULT`, the run logs `TOTAL` |
| 10-16 | Full run crashed after Arbeitnow: `UnicodeEncodeError: 'charmap' codec can't encode '\u2192'` | Piped output on Windows uses cp1252, which has no `→` (or Chinese titles) | CLI forces UTF-8 output; status lines are plain ASCII; regression test simulates cp1252. Every run now also writes `output/logs/werkbot_<date>.log` |
| 10-16 | foorilla filters (Part-time / English / Germany) not in the URL | The site stores filters in the browser session | Use its quick-search parameter and filter by location ourselves |
| 10-16 | `ValidationError: name … input_value=True` in a test | YAML reads the words `on`/`off`/`yes`/`no` as booleans | Never use those as source names (or quote them) |
| 10-16 | `KeyError: 'connecticum'` in all new tests | The new `adapters/__init__.py` was saved over `src/werkbot/__init__.py` (two files with the same name); the adapters were never registered | Restored with `git checkout`; adapters now auto-register, so that file never changes again |
| 10-16 | CI red again (missing final newline) | Formatting depended on remembering to run ruff | Pre-commit hook formats automatically; branch ruleset should block red merges |
| 10-16 | CI red after merging PR #1: `ruff format --check` failed on 14 files | Files pasted into VS Code lost their final newline | `uv run ruff format .`; VS Code now inserts final newlines; `.gitattributes` forces LF. Run `ruff format`, `ruff check`, `pytest` before every commit. |
| 10-16 | germantechjobs page is only 6 KB with no jobs | The site is a JavaScript app; the HTML is an empty shell | Use its JSON API (`/api/jobsLight`), which also has job type + language |
| 10-16 | workingstudentjobs page mixes results with a "Trending this week" sidebar | Scraping all job links would pick up unrelated jobs | Read the result objects from the embedded page data instead of links |
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
| Prefer data embedded in pages (`__NEXT_DATA__`, Next.js payload, JSON-LD) over CSS classes | Class names like `jobTeaser_title__XIgud` change on every deploy; the data structure rarely does |
| Test fixtures are trimmed copies of real pages | Tests check against the actual markup, not what we assume it looks like |
| WeAreDevelopers: read Markdown, not HTML cards | Plain text with labelled fields; far less likely to break on a redesign |
| Fit score, not "chance of getting hired" | No tool can predict hiring honestly |
| Dedup leans towards keeping jobs apart | A missed duplicate shows a job twice; a wrong merge hides a job you might want |
| English and German versions of one posting count as one job when their titles normalize the same | Same position; all links are kept, so Phase 3 can pick the English one |
| Company aliases live in `data/company_aliases.yaml` | Adding an employer's other name is a config change |
| Borderline titles (85–89) need near-identical description text, not embeddings (yet) | Embeddings come with Phase 4; the check is pluggable (`confirm=`) |
| Fuzzy matching (level 4) only across boards | Within one board, two IDs with different titles are almost always two jobs |
| Page-watch items (MPI) are not stored as jobs | They're change alerts, not postings; their snapshot logic stays in `output/watch/` |
| An unknown city is compatible with any city (level 4 only) | Some boards omit the city; the title must still match closely |

**Open:** output format. The plan says Telegram; the later discussion favoured a static website + optional Telegram notification (no server). To decide before Phase 6.

---

## Known gaps

- **WeAreDevelopers has no description** in the list. Fit scoring needs one, so job pages will be fetched later (after dedup + filtering, to avoid ~200 extra requests).
- **Descriptions are missing** for connecticum, e-fellows, Fraunhofer, germantechjobs and workingstudentjobs (list pages only). The probe detail pages show e-fellows + workingstudentjobs have JSON-LD `JobPosting` and Fraunhofer has microdata, so one shared "fetch description" step can cover most sites.
- **germantechjobs has very few student jobs** (≈40 internships of 1093, 38 English). Kept, but it's a low-yield source.
- **Arbeitnow takes ~5 min** (3s × all pages). Fine for a daily run.
- **A board listing one job twice with slightly different titles** stays two jobs (connecticum has the Fraunhofer backend job with and without " - C/C++"). The price of never fuzzy-merging within one board.
- **English/German twins are only merged when the titles normalize the same.** "Student Assistant Windows Binary Fuzzing" = "Studentische Hilfskraft im Bereich Windows Binary Fuzzing" (merged), but "… Backend Development …" vs "… Backend-Entwicklung …" stays two jobs (no translation of topic words).
- **Two Fraunhofer institutes in the same city with the exact same title** would merge (the alias joins all institutes). Both links are kept.
- **The main record's title/description may be the German version** when both versions come from the same kind of source. Phase 3 should use `job_sources.data` to prefer the English one.
- **Company names that differ beyond legal forms** ("Deloitte" vs "Deloitte Wirtschaftsprüfungsgesellschaft") only match after adding an alias. `werkbot dedup --dry-run` shows what merged, to spot misses.
- **Expiry counts days since last seen, for any source.** If a source is broken for 14 days, its jobs expire (and come back as `reposted`).

## Next

1. Run `werkbot dedup --dry-run --groups 30` on a real full run and check the merged groups; add company aliases for misses
2. Phase 3: filters (language, location, role) + shared "fetch description" step for sources whose lists have no description