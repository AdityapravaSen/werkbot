"""Safety net for every test: never touch your real data or call real services.

Without this, a test that runs a CLI command would use data/werkbot.db, your real
data/master_resume.yaml and the LLM key in .env (and fetch job pages for your real jobs).
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch, request):
    from werkbot.config import get_settings

    monkeypatch.setenv("WERKBOT_DB_PATH", str(tmp_path / "isolated" / "werkbot.db"))
    monkeypatch.setenv("WERKBOT_OUTPUT_DIR", str(tmp_path / "isolated" / "output"))
    monkeypatch.setenv("WERKBOT_RESUME_PATH", str(tmp_path / "isolated" / "no_resume.yaml"))
    get_settings.cache_clear()

    if not request.node.get_closest_marker("real_llm_client"):
        from werkbot.llm import client

        def no_llm(cls, *args, **kwargs):
            raise client.LLMUnavailable("tests never call a real LLM")

        monkeypatch.setattr(client.LLM, "from_settings", classmethod(no_llm))

    from werkbot.scoring import embed

    class NoModel:
        def __init__(self, *args, **kwargs):
            raise embed.EmbeddingsUnavailable("tests never load a real model")

    monkeypatch.setattr(embed, "SentenceEmbedder", NoModel)
    yield
    get_settings.cache_clear()
