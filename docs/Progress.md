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
| 3 | Filtering (location, role, language) + job page details | ✅ Built (2026-10-19), live run pending |
| 4 | Fit scoring | ✅ Built (2026-10-20), first run with your resume + LLM key pending |
| 5 | Resume + cover letter tailoring (LaTeX) | ⬜ Not started |
| 6 | Delivery + daily schedule | ⬜ Not started |

**Tests:** 358 passing · **Phase 4 built** (2026-10-20) · **Branch:** `phase-4-scoring`

---

## Sources

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

### 2026-10-20 (Phase 4: after the first real run)
- **First run:** 87 ads read by Gemini Flash-Lite in 35 min, then it stopped at "max_per_run (100)". 13 requests had failed with **503 "high demand"** or timeouts, and they counted towards the 100.
- **Busy server = retry:** a 503 / timeout / dropped connection is retried after 10 s, then 30 s. Only ads read count towards `max_per_run`.
- **Fallback models:** `WERKBOT_LLM_MODEL=gemini/...,groq/...` (comma-separated). A rate limit or 3 errors in a row hand over to the next model, which answers the same ad.
- **Best candidates first:** the run's LLM budget went to "Bauingenieurwesen" and "Sozialpädagogik" jobs (filter decision review) because ads were read in database order. Now kept jobs come first, then jobs whose role is clearly right, then by a quick estimate (skill words + semantic).
- **Semantic anchors** in `scoring.yaml` rewritten in English in the same form as the compared text (title, tasks, requirements): a "Compliance" job scored 69 because the old anchors were short German ads, so every English summary looked close to the resume.
- "Regression testing" counted as the skill Regression; now Testing. A requirement named twice is named once in the reason.
- **Second run:** every ad with text has now been read by the LLM (113); the other 204 jobs have **no ad text** (WeAreDevelopers 76, connecticum 74, foorilla 52). The semantic part works (spread 0.15–0.9 for ads with text, data/software jobs on top).
- **Third run** (with `werkbot filter` done): location/role penalties apply now (Compliance 65 → 53, Legal Ops 70 → 58).
  - Jobs without ad text: first scored as a title against the resume (always near 0), then without a semantic part, which made them top the list (13 of the top 20 at 78, because their title's skills match). Now they get `semantic.no_ad_text: 0.4`, about the middle of real ads (~62 for a good title).
  - **Why 204 jobs have no ad text** (database check): WeAreDevelopers 76 pages `failed`, foorilla 52 `no_description`, connecticum skipped on purpose. foorilla's job page is an empty frame; the ad is an htmx fragment the site loads with the header `HX-Request: true`, so `filters.yaml` can now set headers per site and foorilla sends it. WeAreDevelopers' pages do contain the ad (checked one), so the reason for the failures is still open: `werkbot filter` now prints the failure reasons ("wearedevelopers.com: HTTP 403 ×76"), and `--retry-details` tries pages again without waiting 3 days.
  - Result: foorilla fixed (54 new descriptions; keep 22 → 58). WeAreDevelopers: `HTTP 403` from Cloudflare for every program, also with normal browser headers. Not worked around (that would mean pretending to be a browser); tried the site's own route for tools from its `llms.txt` (`/jobs/{id}.md`): it works, but only for WeAreDevelopers' own ads, and every job in our list is an external one (`/jobs/ext/…`, from Indeed, Adzuna, Careerjet) → `410 Job Not Found`; `.md` and `Accept: text/markdown` on the external pages → 403. So `wearedevelopers.com` is skipped for job pages, like connecticum. (`filters.yaml` can now rewrite a site's job link and read Markdown, kept for sites that offer it.)
  - The requirement scale's anchors were too close ("0.830 vs 0.839") because whole ads were compared with single bullets. It now uses short unrelated requirements (`semantic.unrelated_requirements`).
- The filter results in the database were from before Phase 4, so no location/role penalty was applied ("Working Student Compliance" 65). `werkbot score` now says so and asks to run `werkbot filter` once.

### 2026-10-20 (Phase 4: fit score)
- **`werkbot score`** gives every job that passed the filters (keep or review) a **fit score 0–100** and a one-line reason, e.g. `missing: Kubernetes, (AWS) · German ad` (nice-to-haves in brackets). `scrape` runs it after the filters (`--no-score` to skip). Results: table `scores` and `output/scores.csv`, best first. It is a fit score, not a chance of being hired.
- **Score = semantic 40 + coverage 45 + penalties 15**, all set in `data/scoring.yaml`. A part that can't be computed gives its weight to the others.
  - **Coverage** (`scoring/score.py`): each requirement of the ad is checked against the resume: met, half met or missing; must-haves count double. Pulled a little towards 50 % (`prior_weight`) so an ad that names one skill doesn't score 0 or 100.
  - **Penalties:** from the filters (German level, German ad, hybrid far away, unclear role) and the ad (senior position, more years than the resume shows, no ad text yet).
  - **Semantic** (`scoring/embed.py`, optional extra `ml`): local multilingual model (`intfloat/multilingual-e5-small`, works for German ads too). Raw similarity is rescaled each run between "as close as some unrelated ads" (0) and "as close as an ideal ad built from your resume" (1), so the scale works for any model.
- **Requirements from the LLM** (`scoring/requirements.py`, `llm/client.py`, `llm/prompts/requirements.md`): one request per ad lists tasks, must-haves and nice-to-haves as JSON (checked with pydantic, asked again once if broken). Saved by the ad's text in the new table `job_requirements`, so **each ad costs one request ever**. Free-tier friendly: 8 requests/minute, 100 per run, a rate limit (429) or 3 connection errors in a row stop the LLM for the run, an ad the model can't answer properly is retried after 7 days. **Only the ad is sent; the resume never leaves the computer.**
- **Without an LLM key** (or after the run's limit) requirements come from skill words: in the profile part of the ad they are must-haves, in "nice to have" / tasks they are nice-to-haves, in "we offer" / "about us" they are ignored.
- **Skill words** (`data/skills.yaml`, `scoring/skills.py`): ~120 skills with their spellings (`K8s` = Kubernetes, `Datenbanken` = Databases) and what they imply (`PySpark` → Spark → Big Data), so "Spark" in an ad is met by "PySpark" on the resume.
- **Resume** (`scoring/resume.py`): skills from the skills lists, tags, roles and bullet texts; years of experience (overlapping jobs counted once); "enrolled student" from an ongoing degree.
- **Database:** new table `job_requirements`; `scores` gained `semantic`, `coverage`, `penalty`, `method`, `details` (every requirement and penalty as JSON); `job_filters` stores each filter's own decision. Added automatically.
- **Dependencies:** `instructor` removed (plain JSON + pydantic is enough and works the same for Gemini, Groq and Ollama); `python-dotenv` listed (loads the API keys from `.env`). Default model `gemini/gemini-flash-lite-latest` (largest free quota).
- **Tests can no longer touch your data** (`tests/conftest.py`): every test gets its own empty database, output folder and no resume, and can't call a real LLM or load a real model.

### 2026-10-19 (Phase 3: filters)
- **All rules in `data/filters.yaml`**, nothing hardcoded. Every job gets `keep`, `review` (probably fits, something unclear) or `drop`, with the reasons. `werkbot filter` runs them; `scrape` runs them automatically after saving. All results: `output/filtered.csv`.
- **Location** (`filters/location.py`, `filters/places.py`): within 200 km of Kaiserslautern (straight line), or a place in the train list that is ≤ 3 h by DB (Köln 2:27, Ulm 2:22, Düsseldorf 2:55, Würzburg 2:57; fastest connections from bahn.de). Fully remote jobs are kept anywhere in Germany; hybrid jobs outside the area go to review; other countries are dropped. Places are looked up offline in `data/places.tsv` (13,400 German towns + big cities nearby, from GeoNames).
- **Work mode**: fully remote / hybrid / on site, from the location, title and description ("100% remote", "Homeoffice möglich", "kein Home Office"), and from each board's remote flag (`remote_means` in `sources.yaml`: on Arbeitsagentur, e-fellows and WeAreDevelopers it only means "home office possible").
- **Language** (`filters/language.py`): reads what the ad says ABOUT German: fluent / very good / C1 / native → drop; required without a level → review; good / B1 / B2 → keep; a plus / basic / "nicht erforderlich" → keep; a German ad that says nothing → review. The word "Deutsch" alone decides nothing. If one board has the ad in English, that version decides.
- **Role** (`filters/role.py`): working student / internship / part-time (title, job type or description); apprenticeship, dual study, PhD and full-time-only are dropped; the field needs tech words in the title (word lists in the config). Tech-only boards count as tech when the title has no field words.
- **Job page details** (`details.py`): for jobs that pass location + role but have no description, the job page is read (JSON-LD JobPosting → microdata → known page parts → main text). Polite: 2 s between requests to one site, at most 150 pages per run (LinkedIn 5 s / 40), a site that rate-limits is skipped for the run, failed pages are retried after 3 days. foorilla's missing company names are filled in from the page, and the job is merged if it turns out to be a duplicate.
- **Database:** new table `job_filters`; `jobs.details_status` / `details_at`. Added automatically.
- Checked against the 1,069 jobs of the first full run: 103 dropped for fluent German (all checked by hand), 367 too far, 219 not tech; nearly all "review" are jobs without a description yet.

### 2026-10-19 (Phase 2: checked against a full real run)

### 2026-10-19 (Phase 2: checked against a full real run)
All 1,127 jobs from the first full run were checked for wrong merges and missed duplicates. Every merge was correct. Fixes from what was found:
- **Places:** state codes (`Karlsruhe, BW, DE`), regions (`Leonberg, Württemberg`), street addresses and words like "HQ" / "Zentrale" are no longer read as cities. Umlauts are ignored in city names (`Boblingen` = `Böblingen`), and LinkedIn's `Coblenz` = `Koblenz`. Two jobs are in the same place only if one's main city is among the other's cities.
- **Companies:** `E.ON` was read as "ON" (the "E." was taken for "e.V."); fixed, and `e.K.` is removed. Also removed: "Deutschland", "Technologies", "Holding", "Wirtschaftsprüfungsgesellschaft", "Niederlassung …" (so `KPMG AG Wirtschaftsprüfungsgesellschaft` = `KPMG Deutschland`). New aliases: Haufe, Scout24, Infineon. LinkedIn job-ad resellers (Jobster, HeyJobs, Instaffo) and WeAreDevelopers' "Allgemeine Informationen" heading count as an unknown company.
- **Titles:** `(f/m/div.)`, `[M /W /D]`, WeAreDevelopers' cut-off `iv)`, `Auszubildende/r`, and `Werkstudierende` / `Werkstudium` are handled.
- Result on that run: 1,127 jobs → 1,069 (58 duplicates merged).

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
| 10-20 | `test_missing_api_key` failed on your laptop: "DID NOT RAISE" | LiteLLM loads `.env` itself when imported, and counts an empty `GROQ_API_KEY=` (as in `.env.example`) as a key. A real run would then have sent requests with no key | werkbot loads `.env` itself (`LITELLM_MODE=PRODUCTION`) and treats empty `*_API_KEY` values as missing |
| 10-20 | Tests took 212 s on your laptop (4 s here) | A CLI test ran `scrape` without its own database setting, so it used your real `data/werkbot.db`: it filtered your 1,069 jobs and fetched job pages from the internet. With Phase 4 it would also have read your resume and used your LLM key | `tests/conftest.py` gives every test a temporary database, output folder and resume path, and blocks the real LLM and embedding model |
| 10-20 | Jobs without ad text scored 100 in a check on the real jobs | No requirements and no semantic part left only the penalties, which were all fine | Coverage starts at 50 % (`prior_weight`); "no ad text yet" is a penalty |
| 10-20 | "Optimierung" (process optimisation) counted as a maths skill in many non-tech ads | Too broad a spelling | The skill is "Mathematical Optimization" (operations research, linear programming) |
| 10-20 | LiteLLM warned on every Gemini request about `temperature` | Gemini 3+ prefers its default temperature | Temperature 0 only for other providers; LiteLLM warnings hidden |
| 10-19 | "German is a plus" counted as "fluent" in "Fluent English, German is a plus" | A level word was matched to German just because both were in one sentence | Each level word belongs to one language: the one right after it ("fluent English") or right before it ("German is a plus"), within 30 characters |
| 10-19 | "sehr gute Deutsch- oder Englischkenntnisse" dropped | "oder" means either language is enough | Languages joined by "oder/or" make German a plus |
| 10-19 | "Werkstudent Sales/Business Development" counted as tech | The tech word "develop*" | Removed; longer phrases ("business development") win over words inside them |
| 10-19 | "Werkstudent Softwareentwicklung" not counted as tech | German compound: "software" is not a separate word | Word lists support `software*` and `*informatik` |
| 10-19 | "Politik und Gremien", "Facility Management" kept as tech | Every description mentions "Daten", "MS Office", "technisch" | Tech words in the description alone never keep a job (→ review); "technical/technisch" removed |
| 10-19 | connecticum job pages read as descriptions | The page has no ad text, only a list of other jobs | connecticum is skipped for details; page text is only used if it mentions the job's title |
| 10-19 | A recruiter's name and e-mail in a test page | Real pages were copied as fixtures | Replaced with placeholders |
| 10-19 | "Karlsruhe, BW, DE" and "Stuttgart, BW, DE" counted as the same place | "BW" (and "Württemberg", street names …) was read as a second city, and any shared city counted | State codes and regions are not cities; only a job's main city is compared |
| 10-19 | E.ON became company "on" | The "E." looked like the start of "e.V." | Remove only "e.V." / "e.K." as a whole |
| 10-19 | foorilla jobs that are on other boards were not merged (24 pairs) | foorilla's list has no company name | Kept apart for now; Phase 3 reads the company from the job page, then the normal rules apply |
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
| Three decisions (keep / review / drop), not two | A filter that is unsure shouldn't hide a job; "review" jobs are shown with the reason |
| Places offline (`data/places.tsv`), not a geocoding API | No API key, no rate limit, same answer every time; rebuilt with `scripts/build_places.py` |
| Train times as a short list in the config, not a live DB API | The free DB APIs are unofficial and often down; the 10 relevant cities were looked up once |
| Language rules per level, not "contains Deutsch" | Many English-friendly ads mention German as "a plus" or ask for "gute Deutschkenntnisse" |
| Only the title decides the field | Descriptions always mention tech-sounding words (MS Office, Daten, Digitalisierung) |
| Dedup leans towards keeping jobs apart | A missed duplicate shows a job twice; a wrong merge hides a job you might want |
| English and German versions of one posting count as one job when their titles normalize the same | Same position; all links are kept, so Phase 3 can pick the English one |
| Company aliases live in `data/company_aliases.yaml` | Adding an employer's other name is a config change |
| Borderline titles (85–89) need near-identical description text, not embeddings (yet) | Embeddings come with Phase 4; the check is pluggable (`confirm=`) |
| Fuzzy matching (level 4) only across boards | Within one board, two IDs with different titles are almost always two jobs |
| Page-watch items (MPI) are not stored as jobs | They're change alerts, not postings; their snapshot logic stays in `output/watch/` |
| An unknown city is compatible with any city (level 4 only) | Some boards omit the city; the title must still match closely |
| The LLM only reads the ad; matching against the resume happens locally | Free tiers allow few requests and may use what is sent; an ad's requirements don't change when the resume does, so one saved answer per ad is enough |
| Requirements saved by the ad's text, not the job ID | Survives merges, and the same ad on two boards costs one request |
| Skill words with "implies" instead of asking the LLM whether the resume meets each requirement | Free, instant, the same every run, and explainable ("Spark: met by PySpark") |
| Embedding similarity rescaled per run with anchor texts | Raw cosine values of each model sit in a narrow band; anchors make 0 and 1 mean the same with any model |
| An ad's requirements are not "met" by similar text alone, only half met | A similar bullet is a hint, not proof |

**Open:** output format. The plan says Telegram; the later discussion favoured a static website + optional Telegram notification (no server). To decide before Phase 6.

---

## Known gaps

- **WeAreDevelopers has no description** in the list. Fit scoring needs one, so job pages will be fetched later (after dedup + filtering, to avoid ~200 extra requests).
- **Descriptions are missing** for connecticum, e-fellows, Fraunhofer, germantechjobs and workingstudentjobs (list pages only). The probe detail pages show e-fellows + workingstudentjobs have JSON-LD `JobPosting` and Fraunhofer has microdata, so one shared "fetch description" step can cover most sites.
- **germantechjobs has very few student jobs** (≈40 internships of 1093, 38 English). Kept, but it's a low-yield source.
- **Arbeitnow takes ~5 min** (3s × all pages). Fine for a daily run.
- **Job pages not yet checked live** (no saved copy): WeAreDevelopers, foorilla, LinkedIn, DFKI. The generic reader handles them (or leaves them empty); the first real run will show how well. LinkedIn may block page requests; those jobs then stay "review".
- **WeAreDevelopers job pages can't be read** (Cloudflare 403 for programs; its Markdown route covers only its own ads, not the external ones in our list). Its jobs are scored from the title ("no ad text yet") unless another board has the same job. Later option: fetch the "Apply" link when it is a company career site (~15 % of its jobs; most go to Indeed / Adzuna / Careerjet).
- **connecticum has no description anywhere** (not in the list, not on the job page), so its jobs stay "review" for the language. The page says the company's language ("Firmensprache"), which could be used later.
- **Some small municipalities are missing** from the places table (e.g. Ammerbuch, Karlstein am Main, Parsdorf); their jobs go to review.
- **Same-name places** are picked by state hint, then population ("Wangen, Kreis Göppingen" picks the wrong Wangen). Rare.
- **Hybrid jobs outside the area** go to review (55 in the first run, mostly Munich/Berlin/Hamburg). Set `hybrid_outside_area: drop` to hide them.
- **foorilla has no company names** in its list; the job page fills them in (Phase 3 details), then duplicates are merged.
- **Ramstein's permanent and temporary "Elektroniker" posts** (same title and city, two IDs) are merged into one job. Both links are kept; the descriptions differ.
- **A board listing one job twice with slightly different titles** stays two jobs (connecticum has the Fraunhofer backend job with and without " - C/C++"). The price of never fuzzy-merging within one board.
- **English/German twins are only merged when the titles normalize the same.** "Student Assistant Windows Binary Fuzzing" = "Studentische Hilfskraft im Bereich Windows Binary Fuzzing" (merged), but "… Backend Development …" vs "… Backend-Entwicklung …" stays two jobs (no translation of topic words).
- **Two Fraunhofer institutes in the same city with the exact same title** would merge (the alias joins all institutes). Both links are kept.
- **The main record's title/description may be the German version** when both versions come from the same kind of source. Phase 3 should use `job_sources.data` to prefer the English one.
- **Company names that differ beyond legal forms and the removed words** only match after adding an alias to `data/company_aliases.yaml`. `werkbot dedup --dry-run` shows what merged, to spot misses.
- **Expiry counts days since last seen, for any source.** If a source is broken for 14 days, its jobs expire (and come back as `reposted`).
- **The semantic part is not yet checked with the real model** (no Hugging Face access in the build environment); tested with fake embeddings. The first run prints the scale ("unrelated ads 0.78 = 0, ideal ad 0.91 = 1"); if most jobs end up near 0 or 1, `unrelated_ads` in `scoring.yaml` can be changed.
- **The LLM prompt is not yet checked on real ads** (tested with fake answers). Check a few rows of `scores.csv` against their ads after the first run.
- **Free LLM limits are small and change often** (Gemini Flash-Lite roughly hundreds of requests a day, Groq ~1,000; check aistudio.google.com/rate-limit). The first run reads at most 100 ads; the rest follow on later runs, scored with skill words meanwhile.
- **Skill words cover tech skills only**; a requirement like "experience in a startup" can only be half met through similarity (needs the `ml` extra), otherwise it is not counted.

## Next

1. Fill in `data/master_resume.yaml`, add a free LLM key to `.env`, `uv sync --group dev --extra ml`, then `werkbot score`; check the top 20 and some reasons against their ads, tune `data/scoring.yaml` / `data/skills.yaml`
2. Phase 5: tailored LaTeX resume and cover letter