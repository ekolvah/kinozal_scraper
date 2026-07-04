import json
import unittest
from pathlib import Path

_SOURCES_JSON = Path(__file__).resolve().parent.parent / "sources.json"


class TestConfigSourceType(unittest.TestCase):
    def test_enabled_github_source_uses_dedicated_type(self) -> None:
        # #275: the github source must carry a dedicated `github_popular` type,
        # not the generic format-keyed `json` bucket, so no other JSON source can
        # silently join it (grain of steam's `steam_charts`).
        config = json.loads(_SOURCES_JSON.read_text(encoding="utf-8"))
        github = next(s for s in config["sources"] if s["id"] == "github_new_popular")
        self.assertEqual(github["type"], "github_popular")
