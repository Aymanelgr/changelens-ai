"""Checks on uploaded documents. The file name and browser MIME type are attacker-controlled,
so the decision rests on the actual bytes."""

from pathlib import PurePath

_BINARY_SIGNATURES = (b"MZ", b"\x7fELF", b"PK\x03\x04", b"%PDF-", b"\x89PNG", b"\xff\xd8\xff")
TEXT_EXTENSIONS = {".md", ".txt"}


class DocumentRejected(ValueError):
    pass


def safe_filename(filename: str | None) -> str:
    """Keeps the base name only. Used for display, never as a path on disk."""
    name = PurePath((filename or "").replace("\\", "/")).name.strip()
    if not name:
        raise DocumentRejected("Nom de fichier manquant.")
    return name[:255]


def validate_document(filename: str, data: bytes, max_bytes: int) -> tuple[str, str | None]:
    """Returns (extension, decoded text), with no text for a PDF, which is read page by page.
    Raises DocumentRejected with a clear message."""
    extension = PurePath(filename).suffix.lower()
    if len(data) > max_bytes:
        raise DocumentRejected(
            f"Fichier trop volumineux (maximum {max_bytes // (1024 * 1024)} Mo)."
        )
    if not data.strip():
        raise DocumentRejected("Le fichier est vide.")

    if extension == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise DocumentRejected("Ce fichier n'est pas un vrai PDF : contenu refusé.")
        return extension, None
    if extension not in TEXT_EXTENSIONS:
        raise DocumentRejected("Type non supporté : utilisez .md, .txt ou .pdf.")
    if data.startswith(_BINARY_SIGNATURES) or b"\x00" in data:
        raise DocumentRejected("Le contenu du fichier est binaire : contenu refusé.")
    try:
        return extension, data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentRejected("Le fichier doit être encodé en UTF-8.") from exc
