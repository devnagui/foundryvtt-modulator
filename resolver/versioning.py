from __future__ import annotations

import re


def parse_version(value: str | int | float | None) -> tuple[int, ...]:
    if value is None:
        return tuple()
    text = str(value).strip().lower()
    if not text:
        return tuple()
    text = text.lstrip("v")
    text = text.replace(".x", "")
    parts = re.findall(r"\d+", text)
    return tuple(int(part) for part in parts)


def _wildcard_prefix(value: str | int | float | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or "x" not in text:
        return None
    text = text.lstrip("v")
    prefix = text.split("x", 1)[0].rstrip(".- ")
    parts = re.findall(r"\d+", prefix)
    if not parts:
        return tuple()
    return tuple(int(part) for part in parts)


def compare_versions(left: str | int | float | None, right: str | int | float | None) -> int:
    left_parts = parse_version(left)
    right_parts = parse_version(right)
    width = max(len(left_parts), len(right_parts))
    padded_left = left_parts + (0,) * (width - len(left_parts))
    padded_right = right_parts + (0,) * (width - len(right_parts))
    if padded_left < padded_right:
        return -1
    if padded_left > padded_right:
        return 1
    return 0


def matches_version_spec(version: str | int | float | None, spec: str | int | float | None) -> bool:
    prefix = _wildcard_prefix(spec)
    if prefix is None:
        return compare_versions(version, spec) == 0
    version_parts = parse_version(version)
    if len(version_parts) < len(prefix):
        return False
    return version_parts[: len(prefix)] == prefix


def exceeds_maximum(version: str | int | float | None, maximum: str | int | float | None) -> bool:
    prefix = _wildcard_prefix(maximum)
    if prefix is not None:
        version_parts = parse_version(version)
        if len(version_parts) < len(prefix):
            return True
        return version_parts[: len(prefix)] != prefix
    maximum_parts = parse_version(maximum)
    version_parts = parse_version(version)
    if len(maximum_parts) == 1 and version_parts:
        return version_parts[0] > maximum_parts[0]
    return compare_versions(version, maximum) > 0


def is_below_minimum(version: str | int | float | None, minimum: str | int | float | None) -> bool:
    prefix = _wildcard_prefix(minimum)
    if prefix is not None:
        version_parts = parse_version(version)
        if len(version_parts) < len(prefix):
            return True
        return version_parts[: len(prefix)] < prefix
    return compare_versions(minimum, version) > 0


def version_major(value: str | int | float | None) -> int | None:
    parts = parse_version(value)
    return parts[0] if parts else None


def version_distance(left: str | int | float | None, right: str | int | float | None) -> tuple[int, ...]:
    left_parts = parse_version(left)
    right_parts = parse_version(right)
    width = max(len(left_parts), len(right_parts))
    padded_left = left_parts + (0,) * (width - len(left_parts))
    padded_right = right_parts + (0,) * (width - len(right_parts))
    return tuple(abs(a - b) for a, b in zip(padded_left, padded_right))
