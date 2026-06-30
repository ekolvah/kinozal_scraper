"""kinozal_scraper — declarative scraping pipelines.

Installable package (src-layout) housing the scraper's modules: pipeline
orchestrators (`*_pipeline`), the shared `generic_pipeline` core, service
adapters (`sheets_storage`, `telegram_notifier`, `gemini_enricher`) and
supporting libraries. Entry points run as `python -m kinozal_scraper.<module>`.

The docstring is load-bearing: `scripts/check_headers.py` requires every module
under the package to carry a top-level docstring (§ module-header gate, #237 B1).
"""
