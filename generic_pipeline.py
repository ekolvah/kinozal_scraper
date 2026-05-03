from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup, Tag

ROW_HEADERS = ["dedupe_key", "title", "url", "metric", "source_id", "notified_at"]


@dataclass
class NormalizedItem:
    dedupe_key: str
    title: str
    source_id: str
    url: str = ""
    description: str = ""
    metric: str = ""
    image_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_row(self, notified_at: datetime | None = None) -> list[Any]:
        ts = (notified_at or datetime.now(UTC)).isoformat()
        return [self.dedupe_key, self.title, self.url, self.metric, self.source_id, ts]


@dataclass
class PipelineResult:
    source_id: str
    items: list[NormalizedItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _json_field(record: dict[str, Any], key: str | None) -> str:
    return _str(record.get(key)) if key else ""


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
        css, attr = selector.rsplit("@", 1)
        el: Tag | None = row.select_one(css.strip()) if css.strip() else row
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


def extract_from_json(
    records: list[dict[str, Any]],
    source_config: dict[str, Any],
) -> PipelineResult:
    source_id: str = source_config["id"]
    fields: dict[str, Any] = source_config.get("fields", {})
    limit: int = int(source_config.get("limit", len(records)))
    result = PipelineResult(source_id=source_id)

    for record in records[:limit]:
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


def extract_from_html(
    html: str,
    source_config: dict[str, Any],
) -> PipelineResult:
    """Extract items from an HTML payload.

    source_config must include:
      row_selector  – CSS selector for the repeating item container
      dedupe_key    – CSS selector (with optional @attr) for the dedup key
      fields.title  – CSS selector (with optional @attr) for the title
    """
    source_id: str = source_config["id"]
    fields: dict[str, Any] = source_config.get("fields", {})
    limit: int = int(source_config.get("limit", 0))
    row_selector: str = source_config.get("row_selector", "")
    result = PipelineResult(source_id=source_id)

    if not row_selector:
        result.errors.append(f"[{source_id}] missing row_selector for html source")
        return result

    soup = BeautifulSoup(html, "html.parser")
    rows: list[Tag] = list(soup.select(row_selector))
    if limit:
        rows = rows[:limit]

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
                url=_html_field(row, fields.get("url")),
                description=_html_field(row, fields.get("description")),
                metric=_html_field(row, fields.get("metric")),
                image_url=_html_field(row, fields.get("image_url")),
                raw={},
            )
        )

    if not result.items and not result.errors:
        result.errors.append(f"[{source_id}] extraction produced zero items")

    return result
