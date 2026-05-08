# Plan: Clean kinozal title metadata (Issue #41)

## Context

Kinozal.tv encodes the full metadata in the `<a title="…">` attribute:

```
Гнев (1 сезон: 1-7 серии из 7) / Man on Fire / 2026 / ДБ (Videofilm Int.), CT / WEB-DLRip
```

The first segment before the first ` / ` is the only human-readable title.
The rest (original name, year, translation, format) is technical noise that leaks into Telegram messages and Sheets.

**Hard constraint:** `NormalizedItem.dedupe_key` must stay raw — it is the key matched against existing Sheets rows. Cleaning it would cause re-delivery of already-seen content.

**Design principle (min-bug, min-token):** one pure function in `text_utils.py`, called once in `kinozal_pipeline.py`, tested directly. No generic-pipeline changes, no new abstractions.

---

## Files to change (3 only)

| File | Change |
|---|---|
| `text_utils.py` | +1 pure function |
| `kinozal_pipeline.py` | 2 edits: import + clean loop after extraction; simplify `enrich_with_trailer` |
| `tests/test_kinozal_pipeline.py` | 3 edits: updated `_item` helper, updated `_FetchingPipeline`, new test class |

---

## Change 1 — `text_utils.py`

Add after the existing `title_year_matches` function:

```python
def extract_kinozal_title(raw: str) -> str:
    return raw.split(" / ")[0].strip()
```

Separator is ` / ` (with spaces) to avoid splitting on `/` inside translation credits like `ДБ (Videofilm Int.), CT`.

---

## Change 2 — `kinozal_pipeline.py`

### 2a — import

Add new import line:

```python
from text_utils import extract_kinozal_title
```

### 2b — clean titles after extraction (inside the URL loop, after `extract_from_html`)

Current (`kinozal_pipeline.py` lines 95-99):
```python
result = extract_from_html(html_text, source)
if not result.ok:
    logger.error("[%s] extraction errors: %s", source["id"], result.errors)
    continue
all_items.extend(result.items)
```

New:
```python
result = extract_from_html(html_text, source)
if not result.ok:
    logger.error("[%s] extraction errors: %s", source["id"], result.errors)
    continue
for item in result.items:
    item.title = extract_kinozal_title(item.title)
all_items.extend(result.items)
```

`dedupe_key` is untouched — dedup against Sheets remains correct.

### 2c — simplify `enrich_with_trailer` (lines 52-61)

After the fix, `item.title` is already the clean segment ("Гнев (1 сезон: 1-7 серии из 7)"),
so `split("/")[0]` becomes a no-op and can be dropped.
Year must now come from `item.dedupe_key` (the raw string still contains the year).

```python
def enrich_with_trailer(item: NormalizedItem, youtube: Any) -> str:
    """Look up a YouTube trailer URL. Returns '' on any failure."""
    try:
        clean = item.title.split("(")[0].strip()
        year_match = re.search(r"\b(20\d{2})\b", item.dedupe_key)
        year = int(year_match.group(1)) if year_match else None
        return youtube.get_trailer_url(clean, year=year) or ""
    except Exception as exc:
        logger.error("trailer lookup failed for %r: %s", item.title, exc)
        return ""
```

---

## Change 3 — `tests/test_kinozal_pipeline.py`

### 3a — import

Add to the imports block:
```python
from text_utils import extract_kinozal_title
```

### 3b — update `_FetchingPipeline.run()` (line ~260)

Mirror the change made to production `run_kinozal_pipeline`. After `extract_from_html`, add the cleaning loop:

```python
result = extract_from_html(html, source)
if result.ok:
    for item in result.items:
        item.title = extract_kinozal_title(item.title)
    all_items.extend(result.items)
```

### 3c — update `TestEnrichWithTrailer._item` (line 107-108)

The new contract: `dedupe_key` = raw, `title` = already-cleaned.

```python
def _item(self, raw: str) -> NormalizedItem:
    return NormalizedItem(
        dedupe_key=raw,
        title=extract_kinozal_title(raw),
        source_id="kinozal_movies",
    )
```

All 8 existing `TestEnrichWithTrailer` test cases pass unchanged with this helper update — verified case by case:

| Test | Why it still passes |
|---|---|
| `test_title_cleaned_before_lookup` | raw="Film One / 2024 / BDRip" → title="Film One" → trailer contains "Film_One" ✓ |
| `test_parentheses_stripped` | raw="Film (2024)" → title="Film (2024)" → clean="Film" ✓ |
| `test_exception_returns_empty_string` | no change ✓ |
| `test_year_extracted_from_title_and_passed` | year from dedupe_key="Film One / 2024 / BDRip" → 2024 ✓ |
| `test_no_year_passes_none` | no year in dedupe_key → None ✓ |
| `test_year_in_parentheses_extracted` | year in dedupe_key → 2023 ✓ |
| `test_clean_title_passed_without_year_slash` | title="Great Film" → last_film="Great Film" ✓ |
| `test_2026_film_skips_2015_kingsman_trailer` | year from dedupe_key=2026 → correct trailer ✓ |

### 3d — add `TestExtractKinozalTitle` class

```python
class TestExtractKinozalTitle(unittest.TestCase):
    def test_strips_metadata(self) -> None:
        raw = "Гнев (1 сезон: 1-7 серии из 7) / Man on Fire / 2026 / ДБ (Videofilm Int.), CT / WEB-DLRip"
        self.assertEqual(extract_kinozal_title(raw), "Гнев (1 сезон: 1-7 серии из 7)")

    def test_no_separator_returns_as_is(self) -> None:
        self.assertEqual(extract_kinozal_title("Дюна"), "Дюна")

    def test_single_slash_without_spaces_not_split(self) -> None:
        self.assertEqual(extract_kinozal_title("ДБ (Videofilm/Int.)"), "ДБ (Videofilm/Int.)")
```

### 3e — existing tests that need no changes

- `TestBaseUrlResolution.test_dedupe_key_is_title_attribute`: checks `dedupe_key == "Film One / 2024 / BDRip"` — still passes, `dedupe_key` is never cleaned.
- `TestPipelineDeduplication.*`: use raw titles as `existing_keys` which match `dedupe_key`. `Notification.id = item.dedupe_key` (confirmed `generic_pipeline.py:259`). All pass.
- `TestPipelineNotificationContent.*`: check `<b>` and URL presence — unaffected by title content.

---

## Downstream effects

| Concern | Verdict |
|---|---|
| Sheets `dedupe_key` column | Unchanged — dedup safe, no duplicate re-sends |
| Sheets `title` column | Now stores clean title — better UX in spreadsheet |
| Telegram message text | Clean title in `{title_link}` — this is the fix |
| YouTube trailer lookup | `clean` and `year` still correct (sourced via `dedupe_key`) |
| mypy | All signatures unchanged, `item.title: str` — no type errors |
| ruff | One-liner function, no lint issues |

---

## Verification

```bash
python scripts/ci_check.py
# or targeted:
python -m pytest tests/test_kinozal_pipeline.py -v
python -m pytest tests/test_kinozal_pipeline.py::TestExtractKinozalTitle -v
python -m mypy kinozal_pipeline.py text_utils.py
```
