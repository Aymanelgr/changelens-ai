"""Compares two requirement sets (pipeline step 3). Pure functions, no database."""

import difflib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from app.modules.requirements.facts import NUMBER_PATTERN, compute_change_facts
from app.modules.requirements.parser import normalize_text

# A whole number such as "10 000" stays one token, so the diff shows 10 000 -> 5 000
# instead of the misleading 10 -> 5 with "000" unchanged.
_TOKEN_RE = re.compile(rf"{NUMBER_PATTERN}|\s+|[^\s]+")


class RequirementLike(Protocol):
    req_key: str
    title: str
    text: str


@dataclass(frozen=True)
class RequirementChange:
    req_key: str
    status: str  # ADDED, REMOVED, MODIFIED or UNCHANGED
    title: str
    old_text: str | None
    new_text: str | None
    facts: list[dict[str, Any]]
    word_diff: list[dict[str, str]]


def word_diff(old: str, new: str) -> list[dict[str, str]]:
    old_tokens = _TOKEN_RE.findall(old)
    new_tokens = _TOKEN_RE.findall(new)
    parts: list[dict[str, str]] = []
    matcher = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            parts.append({"op": "equal", "text": "".join(old_tokens[i1:i2])})
            continue
        if i2 > i1:
            parts.append({"op": "removed", "text": "".join(old_tokens[i1:i2])})
        if j2 > j1:
            parts.append({"op": "added", "text": "".join(new_tokens[j1:j2])})
    return parts


def diff_requirements(
    before: Iterable[RequirementLike], after: Iterable[RequirementLike]
) -> list[RequirementChange]:
    old = {r.req_key: r for r in before}
    new = {r.req_key: r for r in after}
    changes = []
    for key in sorted(old.keys() | new.keys()):
        previous, current = old.get(key), new.get(key)
        if previous is None:
            changes.append(
                RequirementChange(key, "ADDED", current.title, None, current.text, [], [])
            )
        elif current is None:
            changes.append(
                RequirementChange(key, "REMOVED", previous.title, previous.text, None, [], [])
            )
        elif normalize_text(previous.text) == normalize_text(current.text):
            changes.append(
                RequirementChange(
                    key, "UNCHANGED", current.title, previous.text, current.text, [], []
                )
            )
        else:
            changes.append(
                RequirementChange(
                    key,
                    "MODIFIED",
                    current.title,
                    previous.text,
                    current.text,
                    compute_change_facts(previous.text, current.text),
                    word_diff(previous.text, current.text),
                )
            )
    return changes
