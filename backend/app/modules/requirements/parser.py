"""Reads requirement files (pipeline step 2). Every rejection names the offending line."""

import hashlib
import re
from dataclasses import dataclass

import yaml

_KEY_RE = re.compile(r"^[A-Za-z]+-\d+$")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+(?P<key>[A-Za-z]+-\d+)\s*(?:[—:–-]\s*(?P<title>.*?))?\s*$")


class RequirementFileError(ValueError):
    def __init__(self, message: str, line: int | None = None):
        super().__init__(f"Ligne {line} : {message}" if line else message)
        self.line = line


@dataclass(frozen=True)
class ParsedRequirement:
    key: str
    title: str
    text: str
    line: int


def normalize_text(text: str) -> str:
    """Whitespace differences (including non-breaking spaces) are not requirement changes."""
    return " ".join(text.split())


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode()).hexdigest()


def _check_requirement(key: str, text: str, line: int, seen: dict[str, int]) -> None:
    if not key:
        raise RequirementFileError("identifiant vide", line)
    if not _KEY_RE.match(key):
        raise RequirementFileError(f"identifiant invalide « {key} » (attendu : REQ-012)", line)
    if not normalize_text(text):
        raise RequirementFileError(f"texte vide pour {key}", line)
    if key in seen:
        raise RequirementFileError(
            f"identifiant dupliqué {key} (déjà défini ligne {seen[key]})", line
        )
    seen[key] = line


def parse_yaml(content: str) -> list[ParsedRequirement]:
    # compose() keeps line numbers, which safe_load() throws away. It also builds
    # no Python objects, so a hostile file cannot instantiate anything.
    try:
        root = yaml.compose(content, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise RequirementFileError("YAML invalide", mark.line + 1 if mark else None) from exc

    if not isinstance(root, yaml.MappingNode):
        raise RequirementFileError("le fichier doit contenir une clé « requirements »")
    items = next(
        (value for key, value in root.value if getattr(key, "value", None) == "requirements"),
        None,
    )
    if not isinstance(items, yaml.SequenceNode):
        raise RequirementFileError("la clé « requirements » doit contenir une liste")

    parsed: list[ParsedRequirement] = []
    seen: dict[str, int] = {}
    for node in items.value:
        line = node.start_mark.line + 1
        if not isinstance(node, yaml.MappingNode):
            raise RequirementFileError("chaque exigence doit être un objet", line)
        fields = {
            key.value: value.value
            for key, value in node.value
            if isinstance(value, yaml.ScalarNode)
        }
        key = str(fields.get("id", "")).strip()
        text = str(fields.get("text", ""))
        _check_requirement(key, text, line, seen)
        parsed.append(ParsedRequirement(key, str(fields.get("title", "")).strip(), text, line))
    if not parsed:
        raise RequirementFileError("aucune exigence trouvée")
    return parsed


def parse_markdown(content: str) -> list[ParsedRequirement]:
    """Format: a heading "## REQ-012 — Title" followed by the requirement text."""
    parsed: list[ParsedRequirement] = []
    seen: dict[str, int] = {}
    current: tuple[str, str, int] | None = None
    body: list[str] = []

    def flush() -> None:
        if current:
            key, title, line = current
            text = "\n".join(body).strip()
            _check_requirement(key, text, line, seen)
            parsed.append(ParsedRequirement(key, title, text, line))

    for number, line in enumerate(content.splitlines(), start=1):
        match = _MD_HEADING_RE.match(line)
        if match:
            flush()
            current = (match.group("key"), (match.group("title") or "").strip(), number)
            body = []
        elif current:
            body.append(line)
    flush()
    if not parsed:
        raise RequirementFileError("aucune exigence trouvée (titre attendu : ## REQ-012 — Titre)")
    return parsed


def parse_requirements(filename: str, content: str) -> list[ParsedRequirement]:
    lowered = filename.lower()
    if lowered.endswith((".yaml", ".yml")):
        return parse_yaml(content)
    if lowered.endswith(".md"):
        return parse_markdown(content)
    raise RequirementFileError("format non supporté : utilisez .yaml, .yml ou .md")
