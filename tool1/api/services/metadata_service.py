from typing import Any


def apply_filters(
    videos: list[dict[str, Any]],
    date_from: str | None = None,
    date_to: str | None = None,
    min_duration: int | None = None,
    max_duration: int | None = None,
    keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    result = []
    for v in videos:
        upload_date = v.get("upload_date") or ""
        duration = v.get("duration") or 0
        title = (v.get("title") or "").lower()
        desc = (v.get("description") or "").lower()

        if date_from and upload_date and upload_date < date_from:
            continue
        if date_to and upload_date and upload_date > date_to:
            continue
        if min_duration is not None and duration < min_duration:
            continue
        if max_duration is not None and duration > max_duration:
            continue
        if keywords:
            text = title + " " + desc
            if not all(kw.lower() in text for kw in keywords):
                continue

        result.append(v)
    return result
