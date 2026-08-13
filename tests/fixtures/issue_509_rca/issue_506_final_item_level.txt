## Context / Why

Run 31663905928 (2026-08-13 03:28 UTC) delivered two audiobooks as movie notifications:
`Сергей Тармашев - Каждому своё` and `Сергей Лукьяненко - Небесное воинство: Седьмой`. The run log
also shows the second symptom of the same defect — the trailer stage searched
`['Сергей Лукьяненко - Небесное воинство: Седьмой 2025 trailer', 'Фантастика 2025 trailer']`, i.e. it
read the book's **genre** segment as an original film title.

**Root cause: the pipeline never determines the content type of an individual release.**
`sources.json:104` extracts every `a[href^='/details.php']` anchor from a listing page, and every stage
downstream (title grammar, trailer lookup, Telegram delivery) treats each extracted row as a film or a
game. In the operator's mixed feed `t=0 Избранные раздачи` that assumption is simply false for the
audiobook, music, library and software rows sharing the same page.

`KINOZAL_EXCLUDED_GENRES=Hidden objects` cannot cover it: the book's details-page genre is
`Космическая фантастика, космические путешествия, научная фантастика` — a genre films also carry.
Genre is a property *within* a content type, not the type.

**Why the previous revision (PR #507) is at the wrong level.** It classifies the *whole feed* by the
selected `t=` and rejects `Избранные раздачи` entirely. Measured on the live feed the operator actually
configured (`top.php?t=0&d=14`, 2026-08-13): 49 of 50 rows are films/series, 1 is a music album. Feed-level
rejection therefore suppresses 49 wanted items to remove 1 unwanted one. The production workaround applied
on 2026-08-13 06:29 (`KINOZAL_URLS` switched `t=0` → `t=1`) has the same shape: it silently replaces the
operator's chosen feed instead of filtering the item. Both are the wrong abstraction level, not a wrong
configuration value.

### Live evidence (2026-08-13, authenticated `kinozal.guru` mirror — `kinozal.tv` resolves neither from GitHub runners nor from the maintainer machine)

1. **`top.php` carries no per-item type.** The listing is one flat `div.bx1.stable` containing
   `<a href="/details.php?id=…" title="…"><img src="/i/poster/…"></a>` — no row grouping, no badge, no
   section header, no type token. A listing-level discriminator does not exist; only `title` and poster
   path are available there.
2. **`details.php` carries an authoritative per-item category marker:**
   `<img class="cat_img_r" onclick="cat(N);" src="/pic/cat/N.gif">`, where `N` is Kinozal's own
   `browse.php?c=N` category id. Verified across every content type present in the operator's feeds:
   `2142272 → 6` (film), `2128508 → 13` (film), `2134435 → 45` (RU series), `2126087 → 3` (music album,
   present in the live `t=0` page), `2112853 → 2` (audiobook, the incident item), `1369136 → 41` (library),
   `2135404 → 23` (game). This is the tracker's own classification of the release, not a heuristic over the
   title.
3. **The full `browse.php` `<select name="c">` taxonomy, read live on 2026-08-13.** Flat `Group - Name`
   shape; groups are `Кино`, `Сериал`, `Мульт`, `Музыка`, `Другое`. The group is the prefix up to the
   **first** ` - ` (names themselves contain ` / `, e.g. `Боевик / Военный`):

   | id | name | id | name |
   |----|------|----|------|
   | 45 | Сериал - Русский | 12 | Кино - Детский / Семейный |
   | 46 | Сериал - Буржуйский | 7 | Кино - Классика |
   | 8 | Кино - Комедия | 48 | Кино - Концерт |
   | 6 | Кино - Боевик / Военный | 49 | Кино - Передачи / ТВ-шоу |
   | 15 | Кино - Триллер / Детектив | 50 | Кино - ТВ-шоу Мир |
   | 17 | Кино - Драма | 38 | Кино - Театр, Опера, Балет |
   | 35 | Кино - Мелодрама | 16 | Кино - Эротика |
   | 39 | Кино - Индийское | 21 | Мульт - Буржуйский |
   | 13 | Кино - Фантастика | 22 | Мульт - Русский |
   | 14 | Кино - Фэнтези | 20 | Мульт - Аниме |
   | 24 | Кино - Ужас / Мистика | 3 | Музыка - Буржуйская |
   | 11 | Кино - Приключения | 4 | Музыка - Русская |
   | 10 | Кино - Наше Кино | 5 | Музыка - Сборники |
   | 9 | Кино - Исторический | 42 | Музыка - Классическая |
   | 47 | Кино - Азиатский | 2 | Другое - АудиоКниги |
   | 18 | Кино - Документальный | 1 | Другое - Видеоклипы |
   | 37 | Кино - Спорт | 23 | Другое - Игры |
   | 32 | Другое - Программы | 40 | Другое - Дизайн / Графика |
   | 41 | Другое - Библиотека | | |

   `0` and `1001`–`1004`, `1006` are aggregate pseudo-options ("Все фильмы", "Вся музыка", …) and are never
   a release category.
4. **Cost is ~zero in the current production configuration.** `_split_by_excluded_genre` already fetches
   `details.php` once per **new** item whenever `KINOZAL_EXCLUDED_GENRES` is non-empty — and it is set in
   production. Reading the category marker from that same response adds no HTTP request, and the taxonomy is
   committed in the repository rather than fetched (see AC3), so the fix adds **no network calls at all**.
   The details fetch happens after dedup, so the unit is new items (7 in the incident run), not extracted
   rows (250), and that unit is unchanged. It applies to new items from every configured feed, not only the
   mixed one; the first run after restoring `t=0` is the peak, and it is the same peak the genre filter
   already pays today.

The maintainer's earlier operability feedback still holds and is kept: category **policy** stays operator-managed
readable configuration in a repository variable, never numeric constants in Python. What changes is the level it
is applied at — the item's own category instead of the feed's selected `t=`. The committed id→name table is
**taxonomy, not policy**: it maps what Kinozal calls things, and no deployment decision lives in it.

## Acceptance criteria

1. Content type is determined per item from the `details.php` category marker (`img.cat_img_r`,
   `onclick="cat(N)"` with `src="/pic/cat/N.gif"` as the fallback source of `N`), never from the listing's
   `t=` parameter. Exactly one marker on the page → use it; zero or several → unknown (see AC6). No
   feed-level category rejection remains in the code — the #507 guard is reverted, including its
   `kinozal_listing_category*` provenance, the `t=` label in the new-item log, and its tests.
2. `KINOZAL_EXCLUDED_ITEM_CATEGORIES` is a semicolon-separated, case-insensitive denylist of readable
   category names with normalized whitespace. A configured **group** (`Музыка`) excludes every `Музыка - …`
   name; matching is normalized equality or `startswith(configured + " - ")`. The variable is renamed rather
   than reused: the old `KINOZAL_EXCLUDED_CATEGORIES` holds `top.php` selector names, which stay
   syntactically plausible under the new code, so reuse would let a forgotten update or a rollback silently
   pair code with the wrong namespace.
3. The id→name table is a committed constant derived from the taxonomy above, not a runtime fetch. Rationale
   to record with it: the authoritative datum is the numeric id, `browse.php` only makes it readable, and a
   live fetch would add an authenticated request whose failure silently disables the whole denylist. Cost of
   the choice: a newly added Kinozal subcategory is unknown until the table is updated — which is exactly
   the behaviour AC5 mandates anyway (unknown → delivered, visibly).
4. A new item in a denied category is not notified and never reaches trailer lookup, but IS stored for dedup
   — the same terminal non-delivery as a genre-filtered item (Principle III), so it is not re-fetched every run.
5. Any category not named in the denylist is delivered regardless of its numeric id, so a future Kinozal
   category cannot be silently suppressed.
6. Fail-open **and** visible (§IV), at two levels:
   - *Per item*: a missing/ambiguous marker, an unparseable id, an id absent from the committed table, or a
     failed details fetch keeps the item with a WARNING naming the item and the cause.
   - *Aggregate*: when the category resolves for **zero** of N ≥ 1 new items, that is selector or auth drift,
     not per-item degradation — append a readable `PipelineResult` error so the run goes red while the items
     are still delivered. This closes the #317 shape, where an anonymous mirror GET returned a 200 login page,
     every genre parsed to `""`, and the filter went blind fail-open with no visible signal.
   - Configured names absent from the committed table append a readable `PipelineResult` error (typo /
     taxonomy drift), delivery unchanged.
7. An empty or unset `KINOZAL_EXCLUDED_ITEM_CATEGORIES` turns the category filter off and logs one INFO line
   — it is **not** an error. Recorded decision, reversing the feed-level revision's rule: an unset denylist is
   a deployment choice, symmetric with `KINOZAL_EXCLUDED_GENRES`, and reddening every unconfigured run would
   alert on configuration rather than on degradation. The delivered-item log line (AC9) keeps a wrong delivery
   diagnosable after the fact.
8. The details page is fetched **at most once per new item** and serves both filters, with the category filter
   running first. The pass runs when **either** denylist is non-empty; only when both are empty is no details
   page fetched. The category filter's correctness must not depend on `KINOZAL_EXCLUDED_GENRES` having a value.
9. Per-item provenance carries `kinozal_item_category` (id) and `kinozal_item_category_name` alongside the
   existing listing url. The per-new-item log line moves **after** the filter pass and states the outcome —
   delivered / denied by category `<name>` / denied by genre `<name>` — because `_dedup_and_log_coverage`
   currently logs before the category is known, and the aggregate "filtered N item(s) by excluded genre" line
   would otherwise mislabel category-denied items.
10. `KINOZAL_EXCLUDED_GENRES` keeps its independent details-page genre behaviour unchanged.
11. Production configuration, in this order: (a) the fix reaches `main`; (b)
    `KINOZAL_EXCLUDED_ITEM_CATEGORIES` is created with
    `Музыка;Другое - АудиоКниги;Другое - Библиотека;Другое - Программы;Другое - Дизайн / Графика;Другое - Видеоклипы`
    — every name verified present in the live taxonomy above, `Другое - Игры` deliberately absent so games keep
    arriving — and read back; (c) the stale `KINOZAL_EXCLUDED_CATEGORIES` variable is deleted; (d) only then is
    restoring `KINOZAL_URLS` to the operator's `t=0` feed proposed to the maintainer, who decides.
12. Current-state `pipeline.md` / `operations.md` and `python scripts/ci_check.py` green.

## Test plan

RED-first nodes in `tests/test_kinozal_pipeline.py`. Details/`browse`-derived HTML as small local fixtures —
the marker plus a few fields; no live network in unit tests.

**Incident end-to-end (the node whose absence let three revisions pass):**

- `TestKinozalItemCategoryE2E::test_mixed_listing_delivers_film_and_suppresses_audiobook` — at
  `run_kinozal_pipeline` level with the in-memory storage/notifier doubles (`testing.md` integration-first):
  a listing fixture with a film row plus the real incident audiobook (id 2112853) → the film is notified, the
  audiobook is **not** notified, both are stored for dedup, and the audiobook never reaches the trailer stage.

**Unit / behaviour nodes:**

- `TestItemCategory::test_category_id_parsed_from_marker_onclick_and_from_src_fallback`
- `TestItemCategory::test_zero_or_multiple_markers_are_unknown_with_warning`
- `TestItemCategoryDenylist::test_group_prefix_denies_every_descendant_name`
- `TestItemCategoryDenylist::test_category_absent_from_denylist_is_delivered_regardless_of_id`
- `TestItemCategoryDenylist::test_categories_set_and_genres_unset_still_filters` (AC8 coupling)
- `TestItemCategoryDenylist::test_details_page_fetched_once_for_category_and_genre`
- `TestItemCategoryDenylist::test_no_extra_http_request_is_issued_for_the_taxonomy` (AC3)
- `TestItemCategoryDenylist::test_failed_details_fetch_keeps_item_with_warning`
- `TestItemCategoryDrift::test_all_items_unresolved_appends_pipeline_error_and_still_delivers` (AC6 aggregate)
- `TestItemCategoryConfig::test_configured_name_absent_from_taxonomy_is_a_visible_error`
- `TestItemCategoryConfig::test_unset_denylist_disables_filter_without_error` (AC7)
- `TestKinozalItemProvenance::test_log_states_delivery_outcome_and_raw_carries_category_id_and_name` (AC9)

Regression: existing film/series/game delivery, genre filter (including the unparsed-genre visibility line),
mirror failover, extraction, dedup and error-isolation tests stay green;
`python -m pytest -q tests/test_kinozal_pipeline.py`; `python scripts/ci_check.py`; live read-back of
`KINOZAL_EXCLUDED_ITEM_CATEGORIES`.

## Implementation outline

1. `git revert` the feed-level commits on `issue-506-bug-kinozal-movie` (`641a4a3`, `dbcd465`, `658e962`,
   `50d209a`, `07a4771`, `b442b5a`, `f414258`). Reverting rather than hand-deleting is what makes AC1
   checkable: it removes `_KinozalCategorySelector`, `_kinozal_response_category`, `_excluded_categories`,
   `_category_excluded`, the rejection block in `_fetch_and_extract`, the `listing_category` /
   `listing_category_name` parameters and raw keys, the `t=` label in `_dedup_and_log_coverage`,
   `TestExcludedCategoriesEnv` / `TestKinozalCategoryGuard` with the `_listing_with_selected_category` helper,
   and `pipeline.md` lines 123-144 — as one mechanical step with nothing left behind.
2. `_parse_item_category(details_html) -> int | None`: find `img.cat_img_r`; exactly one match → read `N` from
   `onclick="cat(N)"`, falling back to `src="/pic/cat/N.gif"`; zero or several → `None`.
3. Commit the id→name table (AC3) with the taxonomy above and a short docstring naming its source and the
   date it was read.
4. Replace `_split_by_excluded_genre` with a single pass over new items that fetches `details.php` once and
   applies category then genre, preserving today's fail-open + WARNING shape and the unparsed-genre INFO line.
   Read both denylists once in `run_kinozal_pipeline` and pass them down — no module-level cache, so nothing
   leaks between tests or runs (§II).
5. Track resolution outcomes across the pass to emit the aggregate drift error (AC6) and the per-item outcome
   log (AC9).
6. Update the PR #507 body to describe the item-level design and the revert.
7. Production steps in the AC11 order, each with a read-back.
8. Update `docs/architecture/pipeline.md` and `docs/architecture/operations.md`.

**Delivery route.** PR #507 is reused, not closed: it is the same logical unit (the #506 fix), on the same
branch, and after the revert its `main...HEAD` diff shows only the item-level design. The branch is not
force-pushed, so the abandoned design stays visible in history as consciously reverted.

## Docs to update

- `docs/architecture/pipeline.md` — item category source (`details.php` marker), the committed taxonomy and
  why it is not fetched, denylist matching, one-fetch-per-item ordering with the genre filter, the two-level
  fail-open visibility, provenance and the outcome log line. The listing-category section added by #507 is
  removed by the revert, not extended.
- `docs/architecture/operations.md` — `KINOZAL_EXCLUDED_ITEM_CATEGORIES` contract, production value, the
  deletion of `KINOZAL_EXCLUDED_CATEGORIES`, separation from `KINOZAL_EXCLUDED_GENRES`, and the note that
  `KINOZAL_URLS` may return to `t=0` once the filter is live.
- ADR: none.

## Out of scope

- Title-, ML- or LLM-based content classification: the tracker states the category itself.
- An allowed-category list: a new Kinozal category must not be suppressed silently.
- Fixing film title grammar for book/music releases (`original_title` reading a genre segment as an original
  title). Recorded as **wontfix until observed**: the `Автор - Название / Жанр / …` shape belongs to the
  categories this fix stops delivering, films and series use `RU / Original / Year / Format`, and the game
  grammar is already handled by the service-segment guard (#412). If it is ever observed on a *wanted*
  category, that is a new issue with a real example attached.
- Per-item content type for non-Kinozal sources.
- Removing already-delivered Telegram messages or Sheets rows.
- Restoring `KINOZAL_URLS` without the maintainer's confirmation.
- Whether `Кино - Эротика` belongs in the denylist: an operator preference, settable without code.

## Architect review

reviewer: Claude architect-reviewer subagent

Two passes. The first reviewed the item-level plan as originally written; the second re-reviewed the sections
this body changed in response. The previous revision's review in this section was a Codex self-review of the
**feed-level** design and is superseded — it validated internal consistency and never questioned the
abstraction level, which is exactly where the defect was (tracked as a process defect in #509).

**Accepted as BLOCKING and folded into the ACs above:**

- The details pass was gated on `KINOZAL_EXCLUDED_GENRES` alone, so unsetting an unrelated variable would have
  turned the new filter into a silent no-op → AC8 now states the gate and a test node pins it.
- Per-item fail-open with only WARNINGs re-created the #317 blind-filter hole; the marker was verified only on
  the authenticated mirror, and the production `fetch_details` path is anonymous for `kinozal.tv` URLs →
  AC6 adds the aggregate zero-of-N drift error that reddens the run.
- A live `browse.php` fetch for readable names was an unspecified auth/host path whose failure silently
  disabled the denylist → resolved by adopting the reviewer's alternative: the taxonomy is committed (AC3),
  which removes the request, the auth question and the parse surface at once.
- The proposed production value contained names not present in the evidence section → the complete live
  taxonomy is now in Context, and AC11 requires every configured name to appear in it.
- The incident had no end-to-end test node, only prose → named first in the Test plan, at
  `run_kinozal_pipeline` level with the real audiobook id.

**Accepted as SHOULD-FIX:** variable renamed to `KINOZAL_EXCLUDED_ITEM_CATEGORIES` with the ordering in AC11
(the `t=1` workaround stays until the fix is in `main`); the "empty variable is an error" rule dropped and the
reversal justified (AC7); the per-item log moved after the filter pass with an explicit outcome (AC9); denylists
read once and passed down instead of a module-level memo (outline 4); zero-or-several markers defined as unknown
(AC1); the revert enumerated as the mechanical removal step (outline 1); the `original_title` symptom recorded
as wontfix-until-observed with the reason (Out of scope).

**Recorded, not adopted:** nothing was rejected outright; one measurement note (the 49:1 ratio is `t=0`-only)
is now covered by the per-feed cost sentence in Context §4.

## ADR

none: a local, reversible configuration policy built on the repository's existing environment-variable pattern.

## Agent handoff

planner: Claude Opus 5 (root-cause analysis with live probes against the kinozal.guru mirror)

validation: `python scripts/validate_issue_sections.py 506` passed before implementation

next role: implementer

handoff: ready

