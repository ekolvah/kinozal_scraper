from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace

from kinozal_scraper.llm_observability import Usage, extract_usage, log_llm_call

_LOGGER = logging.getLogger("kinozal_scraper.llm_observability")


class TestExtractUsage(unittest.TestCase):
    def test_reads_token_counts_from_usage_metadata(self) -> None:
        response = SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=320,
                candidates_token_count=48,
                total_token_count=368,
            )
        )
        self.assertEqual(
            extract_usage(response),
            Usage(prompt_tokens=320, candidates_tokens=48, total_tokens=368),
        )

    def test_missing_usage_metadata_yields_none_fields_no_crash(self) -> None:
        # No usage_metadata attribute at all — SDK can omit it — must degrade, not crash.
        usage = extract_usage(SimpleNamespace())
        self.assertEqual(
            usage, Usage(prompt_tokens=None, candidates_tokens=None, total_tokens=None)
        )

    def test_partial_metadata_missing_field_is_none(self) -> None:
        response = SimpleNamespace(
            usage_metadata=SimpleNamespace(prompt_token_count=100, total_token_count=140)
        )
        usage = extract_usage(response)
        self.assertEqual(usage.prompt_tokens, 100)
        self.assertIsNone(usage.candidates_tokens)
        self.assertEqual(usage.total_tokens, 140)


class TestLogLlmCall(unittest.TestCase):
    def test_emits_structured_line_with_tokens_and_latency(self) -> None:
        usage = Usage(prompt_tokens=320, candidates_tokens=48, total_tokens=368)
        with self.assertLogs(_LOGGER, level="INFO") as cm:
            log_llm_call(
                _LOGGER,
                model="gemini-2.5-flash",
                usage=usage,
                latency_ms=740,
                finish_reason="STOP",
                outcome="ok",
            )
        line = "\n".join(cm.output)
        self.assertIn("llm_call", line)
        self.assertIn("model=gemini-2.5-flash", line)
        self.assertIn("prompt_tokens=320", line)
        self.assertIn("candidates_tokens=48", line)
        self.assertIn("total_tokens=368", line)
        self.assertIn("latency_ms=740", line)
        self.assertIn("finish=STOP", line)
        self.assertIn("outcome=ok", line)

    def test_none_tokens_marked_degraded(self) -> None:
        # §IV: missing token counts must surface as a visible `degraded` marker,
        # not silently log zeros / an unqualified "ok".
        usage = Usage(prompt_tokens=None, candidates_tokens=None, total_tokens=None)
        with self.assertLogs(_LOGGER, level="INFO") as cm:
            log_llm_call(
                _LOGGER,
                model="gemini-2.5-flash",
                usage=usage,
                latency_ms=12,
                finish_reason="STOP",
                outcome="ok",
            )
        self.assertIn("degraded", "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
