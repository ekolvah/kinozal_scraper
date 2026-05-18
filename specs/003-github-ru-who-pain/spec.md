# Feature Specification: Russian "who/pain" notifications for GitHub sources

**Feature Branch**: `codex-issue-88-ru-who-pain`

**Created**: 2026-05-18

**Status**: Draft

**Parent**: #88

**Input**: Operator reading Telegram notifications for GitHub projects gets an
English upstream blurb (or a "what does it do" Russian one-liner for
`github_new_popular`). Neither tells the reader **for whom** the project is
relevant nor **which pain** it removes — the most useful filter for a
human deciding whether to click through.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Telegram tells me audience and pain in Russian (P1)

As the operator browsing Telegram notifications for the `github_projects`
feed, when a new repo lands, I want two short Russian lines — one naming the
intended audience, one naming the pain solved — so I can decide in under a
second whether to open the link.

**Why this priority**: Currently I have to read upstream English description
or open the URL to triage. The whole point of the notifier is to compress
many candidates into a fast scan; the format change converts ~50% scroll-by
items into "skip" without leaving Telegram.

**Independent Test**: Run both pipelines with a stub enricher that returns
`"Для кого: разработчик\nЗачем: ускоряет CI"`. Every sent Notification's
`text` contains both literal lines.

**Acceptance Scenarios**:

1. **Given** a `github_new_popular` item with a non-empty description,
   **When** enrichment runs successfully, **Then** the notification contains
   a `Для кого:` line and a `Зачем:` line, in that order, between the title
   and the stars line.
2. **Given** a `github_trending` item with a non-empty description,
   **When** enrichment runs successfully, **Then** the notification contains
   the same two-line structure (`Для кого:` then `Зачем:`).
3. **Given** any GitHub item, **When** the Gemini enricher returns
   `on_error` (empty string), **Then** the notification still sends — the
   `summary_ru` placeholder resolves to empty and the surrounding template
   collapses adjacent blank lines (`build_notification` already does this).

## Requirements *(mandatory)*

- **FR-001**: Both `github_new_popular` and `github_trending` sources in
  `sources.json` MUST have an `enrich` block writing to `field: "summary_ru"`
  with a prompt that explicitly asks the model to produce exactly two lines
  formatted `Для кого: …` and `Зачем: …`.
- **FR-002**: Both sources' `message_template` MUST reference `{summary_ru}`
  on its own line between the title and the stars line.
- **FR-003**: `github_trending_pipeline.run_github_trending_pipeline` MUST
  accept an `enricher: Enricher | None` parameter and apply enrichment to
  new items using the same loop / quota-exhaustion semantics as
  `json_pipeline._run_single_source` (raise → `on_error` fallback for the
  remaining items, but the notification still sends).
- **FR-004**: `github_trending_pipeline.__main__` MUST build a
  `RotatingGeminiEnricher` when `GOOGLE_API_KEY` is set and pass it through
  to `run_github_trending_pipeline` (matching `json_pipeline.__main__`
  exactly so the cron step behaves identically across sources).
- **FR-005**: When `enricher is None` (dry-run, tests with no enrich), the
  trending pipeline MUST behave as today — no enrichment, notifications sent
  with empty `summary_ru` placeholder. No crash.

### Success Criteria

- **SC-001**: For both GitHub sources, ≥ 95% of sent notifications in one
  production run contain both `Для кого:` and `Зачем:` substrings. Misses
  come only from genuine enrich failures (quota / empty model response) and
  the operator can see them as drift.

## Assumptions

- Gemini reliably follows a strict two-line format when the prompt says
  "ответь ровно двумя строками" — already true for the existing
  `summary_ru` single-line prompt. Format drift is acceptable (pipeline does
  not parse the model output; it is passed through verbatim).
- No row-schema migration: `summary_ru` is `item.raw` only, never written to
  the Sheets row. The `ROW_HEADERS` set in `generic_pipeline.py` is
  unchanged.
- Daily-delta `(+N today)` from #86 stays on its current line; the new
  `summary_ru` block goes above stars.
