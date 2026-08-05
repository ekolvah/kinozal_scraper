"""Общий слой пайплайна: NormalizedItem, PipelineResult, extract_from_*."""

from __future__ import annotations

import html as _html
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup, Tag

ROW_HEADERS = ["dedupe_key", "title", "url", "metric", "source_id", "notified_at"]


@dataclass
class NormalizedItem:
    # Canonical identity of the content (film/game title, repo URL, etc.).
    # Must be stable across repacks, mirrors, and variants of the same item —
    # i.e. two different torrents of the same movie share one dedupe_key.
    # Stored as the primary key in Sheets; new items are filtered against it.
    dedupe_key: str
    title: str
    source_id: str
    url: str = ""
    description: str = ""
    metric: str = ""
    image_url: str = ""
    trailer_url: str = ""  # enriched by caller; not stored in Sheets
    raw: dict[str, Any] = field(default_factory=dict)

    def to_row(self, notified_at: datetime | None = None) -> list[Any]:
        ts = (notified_at or datetime.now(UTC)).isoformat()
        return [self.dedupe_key, self.title, self.url, self.metric, self.source_id, ts]


@dataclass
class SourceMetrics:
    """Per-source run counters behind the operator summary line (#459).

    `fetched` — records/rows the extractor was handed; `extracted` — those it
    turned into items; `existing` / `new` — how the extracted candidates split
    against storage (`extracted == existing + new`); `sent` / `stored` — what
    delivery and the confirmed-delivery write actually landed.

    `new` counts what was *found*, `sent` what fitted under the source's delivery
    cap: a deferred remainder stays readable instead of vanishing (§IV).
    """

    fetched: int = 0
    extracted: int = 0
    existing: int = 0
    new: int = 0
    sent: int = 0
    stored: int = 0


@dataclass
class PipelineResult:
    source_id: str
    items: list[NormalizedItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # `None` = this pipeline does not measure, which must stay distinguishable
    # from an all-zero measurement (§IV) — the summary skips it rather than
    # reporting a source as "fetched nothing" when nobody counted.
    metrics: SourceMetrics | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _json_field(record: dict[str, Any], key: str | None) -> str:
    return _str(record.get(key)) if key else ""


def _selector_css_part(selector: str | None) -> str | None:
    """The CSS portion of a field selector (`"css@attr"`, `"@attr"`, `"css"`).

    Returns the stripped CSS string that runtime feeds to `select_one`/`select`,
    or a falsy value when there is none (empty selector, or `@attr`-only). Shared
    by `_html_field` (runtime) and `validate_sources_config` (load-time) so both
    compile the exact same selector string.
    """
    if not selector:
        return None
    css = selector.rsplit("@", 1)[0] if "@" in selector else selector
    return css.strip()


def _html_field(row: Tag, selector: str | None) -> str:
    """Extract a field from an HTML row element.

    Selector forms:
      "css"        – text content of the first matched child (or row itself if no match)
      "css@attr"   – attribute of the first matched child
      "@attr"      – attribute of the row element itself
      None / ""    – empty string
    """
    if not selector:
        return ""
    if "@" in selector:
        _, attr = selector.rsplit("@", 1)
        css = _selector_css_part(selector)
        el: Tag | None = row.select_one(css) if css else row
        return _str(el.get(attr) if el else None)
    el = row.select_one(selector)
    return el.get_text(strip=True) if el else ""


def _build_item(
    source_id: str,
    dedupe_key: str,
    title: str,
    url: str,
    description: str,
    metric: str,
    image_url: str,
    raw: dict[str, Any],
) -> NormalizedItem:
    return NormalizedItem(
        dedupe_key=dedupe_key.strip(),
        title=title.strip(),
        source_id=source_id,
        url=url.strip(),
        description=description.strip(),
        metric=metric.strip(),
        image_url=image_url.strip(),
        raw=raw,
    )


def select_new_items(
    candidates: list[NormalizedItem],
    existing: set[str],
    limit: int,
) -> tuple[list[NormalizedItem], int, int]:
    """Split candidates against storage and cap what gets delivered (#459).

    Returns `(selected, existing_count, new_count)`. The cap applies to the
    **new** items, never to the candidate set — applying it first was the root
    cause of a source going permanently silent once its whole top-N sat in
    Sheets. `limit <= 0` means "no cap".

    A key seen twice within one run (the same repo on two search pages) counts as
    existing on its second sighting, so `existing_count + new_count` always
    equals `len(candidates)` — the invariant the operator line is read against.
    """
    seen = set(existing)
    selected: list[NormalizedItem] = []
    existing_count = 0
    new_count = 0
    for item in candidates:
        if item.dedupe_key in seen:
            existing_count += 1
            continue
        seen.add(item.dedupe_key)
        new_count += 1
        if limit <= 0 or len(selected) < limit:
            selected.append(item)
    return selected, existing_count, new_count


def _effective_limit(override: int | None, source_config: dict[str, Any], fallback: int) -> int:
    """Resolve the `limit=` sentinel shared by both extractors (#459).

    `None` → the source's own `limit` (what every config-driven pipeline wants);
    anything explicit wins, and `0` means "no truncation". The sentinel is
    resolved in one place because the two extractors used to disagree about a
    falsy limit: HTML read it as "no truncation", JSON would have sliced to
    nothing and reported the run as "produced zero items"."""
    if override is not None:
        return override
    return int(source_config.get("limit", fallback))


def extract_from_json(
    records: list[dict[str, Any]],
    source_config: dict[str, Any],
    *,
    limit: int | None = None,
) -> PipelineResult:
    source_id: str = source_config["id"]
    fields: dict[str, Any] = source_config.get("fields", {})
    effective_limit = _effective_limit(limit, source_config, len(records))
    result = PipelineResult(source_id=source_id)
    considered = records if effective_limit <= 0 else records[:effective_limit]

    for record in considered:
        dedupe_key = _json_field(record, source_config.get("dedupe_key"))
        title = _json_field(record, fields.get("title"))

        if not dedupe_key or not title:
            result.errors.append(
                f"[{source_id}] record missing required field(s): "
                f"dedupe_key={dedupe_key!r} title={title!r}"
            )
            continue

        result.items.append(
            _build_item(
                source_id=source_id,
                dedupe_key=dedupe_key,
                title=title,
                url=_json_field(record, fields.get("url")),
                description=_json_field(record, fields.get("description")),
                metric=_json_field(record, fields.get("metric")),
                image_url=_json_field(record, fields.get("image_url")),
                raw=record,
            )
        )

    if not result.items and not result.errors:
        result.errors.append(f"[{source_id}] extraction produced zero items")

    return result


def _resolve_url(value: str, base_url: str) -> str:
    """Join value against base_url if value is relative; absolute URLs pass through."""
    return urllib.parse.urljoin(base_url, value) if base_url and value else value


def extract_from_html(
    html: str,
    source_config: dict[str, Any],
    *,
    limit: int | None = None,
) -> PipelineResult:
    """Extract items from an HTML payload.

    source_config must include:
      row_selector  – CSS selector for the repeating item container
      dedupe_key    – CSS selector (with optional @attr) for the dedup key
      fields.title  – CSS selector (with optional @attr) for the title

    Optional:
      base_url      – prefix for resolving relative url/image_url values
    """
    source_id: str = source_config["id"]
    fields: dict[str, Any] = source_config.get("fields", {})
    effective_limit = _effective_limit(limit, source_config, 0)
    row_selector: str = source_config.get("row_selector", "")
    base_url: str = source_config.get("base_url", "")
    result = PipelineResult(source_id=source_id)

    if not row_selector:
        result.errors.append(f"[{source_id}] missing row_selector for html source")
        return result

    soup = BeautifulSoup(html, "html.parser")
    rows: list[Tag] = list(soup.select(row_selector))
    if effective_limit > 0:
        rows = rows[:effective_limit]

    for row in rows:
        dedupe_key = _html_field(row, source_config.get("dedupe_key"))
        title = _html_field(row, fields.get("title"))

        if not dedupe_key or not title:
            result.errors.append(
                f"[{source_id}] row missing required field(s): "
                f"dedupe_key={dedupe_key!r} title={title!r}"
            )
            continue

        result.items.append(
            _build_item(
                source_id=source_id,
                dedupe_key=dedupe_key,
                title=title,
                url=_resolve_url(_html_field(row, fields.get("url")), base_url),
                description=_html_field(row, fields.get("description")),
                metric=_html_field(row, fields.get("metric")),
                image_url=_resolve_url(_html_field(row, fields.get("image_url")), base_url),
                raw={},
            )
        )

    if not result.items and not result.errors:
        result.errors.append(f"[{source_id}] extraction produced zero items")

    return result


_URL_FIELDS: frozenset[str] = frozenset({"url", "image_url", "trailer_url"})
_NUMBER_FIELDS: frozenset[str] = frozenset({"metric"})
_HTML_FIELDS: frozenset[str] = frozenset({"title_link", "trailer_link"})
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


@dataclass
class Notification:
    id: str  # = NormalizedItem.dedupe_key
    text: str  # готовый HTML-текст для Telegram
    image_url: str = ""


def _format_field(field_name: str, value: Any) -> str:
    if value is None:
        return ""
    value_str = str(value)
    if field_name in _HTML_FIELDS:
        return value_str
    if field_name in _URL_FIELDS:
        if value_str.startswith(("http://", "https://")):
            return value_str
        return _html.escape(value_str, quote=False)
    if field_name in _NUMBER_FIELDS:
        return value_str
    return _html.escape(value_str, quote=False)


def _html_link(href: str, label: str) -> str:
    return f'<a href="{_html.escape(href, quote=True)}">{_html.escape(label)}</a>'


def build_notification(item: NormalizedItem, template: str) -> Notification:
    title_link = (
        _html_link(item.url, item.title)
        if item.url and item.url.startswith(("http://", "https://"))
        else _html.escape(item.title)
    )
    # An http(s) trailer_url renders a clickable "Trailer" word; a non-http,
    # non-empty value is a §IV miss/failure marker (#138) and reaches the user as
    # visible escaped text, not a collapsed empty line. Empty → empty (sources
    # that never enrich a trailer are unaffected). The renderer stays source-
    # agnostic: it knows "http vs marker vs none", not "kinozal markers".
    if item.trailer_url and item.trailer_url.startswith(("http://", "https://")):
        trailer_link = _html_link(item.trailer_url, "Trailer")
    elif item.trailer_url:
        trailer_link = _html.escape(item.trailer_url)
    else:
        trailer_link = ""
    values: dict[str, Any] = {
        "title": item.title,
        "title_link": title_link,
        "url": item.url,
        "description": item.description,
        "metric": item.metric,
        "dedupe_key": item.dedupe_key,
        "trailer_url": item.trailer_url,
        "trailer_link": trailer_link,
    }
    text = template
    for field_name, raw_value in values.items():
        text = text.replace(f"{{{field_name}}}", _format_field(field_name, raw_value))
    for match in _PLACEHOLDER_RE.finditer(text):
        field_name = match.group(1)
        if field_name not in values:
            raw_value = item.raw.get(field_name)
            text = text.replace(f"{{{field_name}}}", _format_field(field_name, raw_value))
    text = re.sub(r"\n{2,}", "\n", text)
    return Notification(id=item.dedupe_key, text=text.strip(), image_url=item.image_url)
