# werkbot

Finds English-speaking **Werkstudent / HiWi** jobs in Germany across 14 job boards, removes duplicates, scores each job against your resume, and generates a tailored LaTeX resume and cover letter.

```
scrape → normalize → deduplicate → filter → score → tailor → deliver
```

> **Status:** Phase 0 (project setup). See [`docs/PLAN.md`](docs/PLAN.md) for the full plan.

## Setup

Requires [uv](https://docs.astral.sh/uv/) (it installs the right Python for you).

```bash
git clone https://github.com/aritra1311/werkbot.git
cd werkbot
uv sync --group dev          # creates .venv and installs everything
cp .env.example .env         # then add your API keys
cp data/master_resume.example.yaml data/master_resume.yaml
uv run werkbot init-db
uv run werkbot sources
```

Optional extras:

```bash
uv sync --group dev --extra browser    # Playwright, for JavaScript-heavy sites
uv run playwright install chromium
uv sync --group dev --extra linkedin   # python-jobspy
uv sync --group dev --extra ml         # local embeddings (large download)
uv sync --group dev --extra bot        # Telegram
uv sync --group dev --all-extras       # everything
```

## Commands

| Command | What it does |
|---|---|
| `werkbot sources [--all]` | List configured sources and which adapters are built |
| `werkbot init-db` | Create the SQLite database (safe to rerun) |
| `werkbot scrape [-s NAME]` | Scrape all enabled sources, or one |
| `werkbot check-db` | Fail if any duplicate jobs slipped into the database |

## Project layout

```
src/werkbot/
├── adapters/     # one module per job source (Phase 1)
├── dedup/        # normalizers, fingerprints, fuzzy matching (Phase 2)
├── filters/      # language, location, role (Phase 3)
├── scoring/      # fit score (Phase 4)
├── tailoring/    # LaTeX rendering + tectonic (Phase 5)
├── llm/          # LiteLLM wrapper + prompts/
├── delivery/     # website + Telegram (Phase 6)
├── cli.py        # `werkbot` command
├── config.py     # settings from .env
├── db.py         # SQLite schema
├── models.py     # Job, Source
└── sources.py    # loads data/sources.yaml
data/
├── sources.yaml                 # all job sources (edit to add/remove sites)
└── master_resume.example.yaml   # copy to master_resume.yaml (git-ignored)
templates/        # LaTeX templates (Phase 5)
tests/
```

## Privacy

This repo is public. Personal files are git-ignored: `.env`, `data/master_resume.yaml`, `data/*.db`, `output/`. Keep it that way.

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
```
