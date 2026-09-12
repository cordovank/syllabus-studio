"""Stable, readable identifiers."""

from __future__ import annotations

import re
import secrets
import time

_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_len: int = 40) -> str:
    s = _SLUG.sub("-", text.lower()).strip("-")
    return (s[:max_len].rstrip("-")) or "course"


def course_id(title: str) -> str:
    return f"{slugify(title, max_len=32)}-{secrets.token_hex(3)}"


def module_id(index: int) -> str:
    return f"m{index + 1}"


def lesson_id(module_index: int, lesson_index: int) -> str:
    return f"m{module_index + 1}l{lesson_index + 1}"


def timestamp_ms() -> int:
    return int(time.time() * 1000)
