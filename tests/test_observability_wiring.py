"""Anti-drift wiring guards for #278: every entry-point ``__main__`` calls ``init_sentry()``
and the workflow wires ``SENTRY_DSN`` at job level. Static source scan (no heavy imports) —
mirrors ``tests/test_settings_hooks.py``."""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src" / "kinozal_scraper"
_WORKFLOW = _REPO / ".github" / "workflows" / "run-script.yml"

# 5 pipeline modules + summarizer = 6 process entry points run as `python -m kinozal_scraper.X`.
_ENTRYPOINTS = (
    "github_popular_pipeline.py",
    "github_trending_pipeline.py",
    "steam_pipeline.py",
    "soldout_pipeline.py",
    "kinozal_pipeline.py",
    "telegram_summarizer.py",
)


def _main_block(source: str) -> str:
    marker = 'if __name__ == "__main__":'
    idx = source.find(marker)
    assert idx != -1, "entry module has no __main__ block"
    return source[idx:]


class TestEntrypointsWireSentry:
    def test_all_six_entrypoints_call_init_sentry(self) -> None:
        assert len(_ENTRYPOINTS) == 6
        for module in _ENTRYPOINTS:
            path = _SRC / module
            assert path.exists(), f"missing entry module {module}"
            block = _main_block(path.read_text(encoding="utf-8"))
            assert "init_sentry(" in block, (
                f"{module} __main__ must call init_sentry() (#278 observability wiring)"
            )

    def test_workflow_sets_sentry_dsn_at_job_level(self) -> None:
        import yaml

        data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
        job_env = data["jobs"]["run-script"].get("env", {})
        assert "SENTRY_DSN" in job_env, (
            "run-script.yml must set SENTRY_DSN at job level (#278) so all steps inherit it"
        )
