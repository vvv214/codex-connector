from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ParsedMessage:
    kind: str
    argument: str = ""
    raw_text: str = ""
    from_plain_text: bool = False


def parse_message(text: str) -> ParsedMessage:
    raw = text or ""
    stripped = raw.strip()
    if not stripped:
        return ParsedMessage(kind="empty", raw_text=raw)
    if not stripped.startswith("/"):
        return ParsedMessage(kind="continue", argument=stripped, raw_text=raw, from_plain_text=True)

    head, _, tail = stripped.partition(" ")
    command = head[1:].lower()
    argument = tail.strip()
    if command in {"project", "new", "continue", "last", "status", "help"}:
        return ParsedMessage(kind=command, argument=argument, raw_text=raw)
    return ParsedMessage(kind="unknown", argument=argument, raw_text=raw)
