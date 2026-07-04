"""Sentry error-tracking bootstrap (#278).

``init_sentry`` is called once per process at each CLI entry point (the 5 pipelines +
``telegram_summarizer``), *after* ``logging.basicConfig`` and *before* the pipeline body.

Design notes (load-bearing — do not silently break):

* **Capture path.** Per-source failures are caught and logged (``logger.exception(...)``),
  not re-raised, so Sentry's automatic ``excepthook`` never sees them. Capture instead rides
  on sentry-sdk's **default** ``LoggingIntegration`` (``event_level=logging.ERROR``): every
  ``logger.exception`` becomes a Sentry *event with a stacktrace*. The invariant "handlers log
  at ERROR" is machine-enforced by ruff **TRY400** (§IV). ``tests/test_observability.py::
  TestCaptureMechanism`` pins this default so a sentry-sdk bump reddens instead of silently
  dropping alerts. **Do not** pass ``default_integrations=False`` or an ``integrations=`` list
  that drops ``LoggingIntegration`` (N2).
* **Degrade-safe.** No ``SENTRY_DSN`` → no-op + visible INFO, job stays green (the code merges
  and runs before the operator provisions the DSN secret).
* **Never load-bearing for delivery (S1).** A malformed DSN raises ``BadDsn``; we swallow it
  (ERROR-logged, visible) and continue — a monitoring misconfig must not take down the 6
  product pipelines.
* **Flush (N1).** sentry-sdk registers an ``atexit`` flush (``shutdown_timeout`` ~2s); a
  ``sys.exit(1)`` raises ``SystemExit`` → normal interpreter shutdown → atexit runs, so queued
  events flush before the process ends.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping

logger = logging.getLogger(__name__)


def init_sentry(
    env: Mapping[str, str] | None = None,
    *,
    initializer: Callable[..., object] | None = None,
) -> bool:
    """Initialise Sentry if ``SENTRY_DSN`` is set. Returns ``True`` when active.

    ``env`` defaults to ``os.environ``; ``initializer`` defaults to ``sentry_sdk.init``
    (imported lazily so this module imports without the package, and tests inject a fake).
    """
    source = env if env is not None else os.environ
    dsn = source.get("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info("Sentry disabled: no SENTRY_DSN")
        return False

    if initializer is None:
        import sentry_sdk

        initializer = sentry_sdk.init

    environment = source.get("SENTRY_ENVIRONMENT") or "production"
    try:
        initializer(dsn=dsn, environment=environment, traces_sample_rate=0.0)
    except Exception:  # noqa: BLE001 — S1: monitor must not crash product delivery
        logger.exception("Sentry init failed — continuing without error tracking")
        return False

    logger.info("Sentry enabled (environment=%s)", environment)
    return True
