"""kinozal_scraper — declarative scraping pipelines.

Installable package (src-layout) housing the scraper's modules: pipeline
orchestrators (`*_pipeline`), the shared `generic_pipeline` core, service
adapters (`sheets_storage`, `telegram_notifier`, `gemini_enricher`) and
supporting libraries. Entry points run as `python -m kinozal_scraper.<module>`.

The docstring is load-bearing: ruff `D100`/`D104`/`D419` require every module
under the package (this `__init__.py` included) to carry a non-empty top-level
docstring (§ module-docstring gate, #253).
"""
