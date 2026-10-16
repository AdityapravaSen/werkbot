"""Tests for the LinkedIn (jobspy) adapter, the page watcher and `scrape -s` with several sources.

LinkedIn tests replace the jobspy call with fake rows, so they need neither the network
nor python-jobspy/pandas installed (CI installs only the dev dependencies).
"""

from datetime import date

import httpx
from typer.testing import CliRunner

from werkbot.adapters.linkedin import LinkedInAdapter
from werkbot.adapters.page_watch import PageWatchAdapter
from werkbot.config import Settings
from werkbot.models import Source, SourceGroup

NAN = float("nan")


# ---------- LinkedIn ----------

ROWS = [  # same keys as jobspy's DataFrame (python-jobspy 1.3.0)
    {
        "id": "li-4012345678",
        "site": "linkedin",
        "job_url": "https://www.linkedin.com/jobs/view/4012345678",
        "job_url_direct": "https://careers.acme.example/jobs/42",
        "title": "Working Student Machine Learning (m/f/d)",
        "company": "Acme GmbH",
        "location": "Kaiserslautern, Rhineland-Palatinate, Germany",
        "date_posted": date(2026, 10, 14),
        "job_type": "parttime",
        "is_remote": False,
        "description": NAN,  # pandas uses NaN for "missing"
    },
    {
        "id": "li-4099999999",
        "job_url": "https://www.linkedin.com/jobs/view/4099999999",
        "job_url_direct": NAN,
        "title": "Werkstudent Data Engineering",
        "company": "Remote Co",
        "location": "Germany",
        "date_posted": NAN,
        "job_type": NAN,
        "is_remote": True,
    },
    {"id": "li-broken", "title": NAN, "job_url": NAN},  # unusable row: skipped
]


class FakeLinkedIn(LinkedInAdapter):
    def __init__(self, *args, rows=None, error=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows, self.error, self.query = rows or [], error, None

    def _scrape(self, **query):
        self.query = query
        if self.error:
            raise self.error
        return self.rows


def _linkedin(**kwargs) -> FakeLinkedIn:
    source = Source(
        name="linkedin_local",
        group=SourceGroup.LINKEDIN,
        adapter="linkedin",
        url="https://www.linkedin.com/jobs/",
        params={
            "search_term": "working student",
            "location": "Kaiserslautern, Germany",
            "distance": 100,
            "hours_old": 168,
            "notes_for_us": "not a jobspy parameter",
        },
    )
    return FakeLinkedIn(source, Settings(_env_file=None), **kwargs)


def test_linkedin_maps_rows_and_handles_nan():
    adapter = _linkedin(rows=ROWS)
    jobs = adapter.fetch()

    assert [j.source_job_id for j in jobs] == ["li-4012345678", "li-4099999999"]
    first, second = jobs
    assert first.company == "Acme GmbH"
    assert str(first.apply_url) == "https://careers.acme.example/jobs/42"
    assert first.date_posted is not None and first.date_posted.day == 14
    assert first.description == ""  # NaN became empty, not "nan"
    assert first.job_types == ["parttime"]
    assert second.remote is True and second.apply_url is None and second.date_posted is None


def test_linkedin_passes_only_jobspy_parameters():
    adapter = _linkedin(rows=[])
    adapter.fetch()

    assert adapter.query == {
        "search_term": "working student",
        "location": "Kaiserslautern, Germany",
        "distance": 100,
        "hours_old": 168,
    }


def test_linkedin_missing_package_is_a_warning_not_a_crash():
    adapter = _linkedin(error=ImportError("No module named 'jobspy'"))

    assert adapter.fetch() == []
    assert "uv sync" in adapter.warnings[0]


def test_linkedin_block_is_a_warning_not_a_crash():
    adapter = _linkedin(error=RuntimeError("429 Too Many Requests"))

    assert adapter.fetch() == []
    assert "429" in adapter.warnings[0]


# ---------- Page watcher ----------

PAGE = """<html><body>
<nav>Home | People | Research | Teaching</nav>
<main><h1>HiWi / student assistants</h1>
<p>There are a limited number of student assistant positions in our lab.
These jobs usually include work as part of a research project.</p>
<p>Please send your application to d2hiwiapplication[at]mpi-inf.mpg.de.</p>
{extra}
</main>
<footer>Imprint | Data Protection | (c) 2026</footer>
</body></html>"""


def _watcher(tmp_path, html: str) -> PageWatchAdapter:
    source = Source(
        name="mpi_inf_d2",
        group=SourceGroup.WATCH,
        adapter="page_watch",
        url="https://www.mpi-inf.mpg.de/hiwi",
        params={"label": "MPI-INF D2 HiWi page", "organization": "MPI for Informatics"},
    )
    settings = Settings(output_dir=tmp_path, _env_file=None)
    handler = lambda request: httpx.Response(200, text=html)  # noqa: E731
    return PageWatchAdapter(source, settings, transport=httpx.MockTransport(handler))


def test_watch_first_run_only_saves_snapshot(tmp_path):
    adapter = _watcher(tmp_path, PAGE.format(extra=""))

    assert adapter.fetch() == []
    assert adapter.snapshot_path().exists()


def test_watch_unchanged_page_reports_nothing(tmp_path):
    _watcher(tmp_path, PAGE.format(extra="")).fetch()

    assert _watcher(tmp_path, PAGE.format(extra="")).fetch() == []


def test_watch_reports_new_lines(tmp_path):
    _watcher(tmp_path, PAGE.format(extra="")).fetch()
    new = "<h2>Open position</h2><p>HiWi for 3D reconstruction with PyTorch, 10 h/week.</p>"
    jobs = _watcher(tmp_path, PAGE.format(extra=new)).fetch()

    assert len(jobs) == 1
    item = jobs[0]
    assert item.title == "[Page changed] MPI-INF D2 HiWi page"
    assert item.company == "MPI for Informatics"
    assert "3D reconstruction with PyTorch" in item.description
    assert item.job_types == ["page-change"]


# ---------- CLI: scrape -s a -s b ----------


def test_cli_scrape_accepts_several_sources(tmp_path, monkeypatch):
    from werkbot import cli
    from werkbot.config import get_settings

    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "- {name: one, group: D, adapter: not_built_one, url: 'https://a.example'}\n"
        "- {name: two, group: D, adapter: not_built_two, url: 'https://b.example'}\n"
        "- {name: three, group: D, adapter: not_built_three, url: 'https://c.example'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WERKBOT_SOURCES_PATH", str(sources))
    monkeypatch.setenv("WERKBOT_OUTPUT_DIR", str(tmp_path / "out"))
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(cli.app, ["scrape", "-s", "one", "-s", "three"])
        assert result.exit_code == 0, result.output
        assert "one" in result.output and "three" in result.output
        assert "two" not in result.output

        bad = CliRunner().invoke(cli.app, ["scrape", "-s", "nope"])
        assert bad.exit_code == 1 and "nope" in bad.output
    finally:
        get_settings.cache_clear()
