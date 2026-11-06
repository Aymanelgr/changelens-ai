"""Reads an uploaded .zip without ever writing its contents to disk (pipeline step 0).

Reading members into memory removes the zip-slip attack entirely: an entry named
../../evil.py cannot overwrite anything because no path from the archive is ever
used on the filesystem. Dangerous paths are still refused: they signal a bad archive.
"""

import io
import re
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

_SENSITIVE_NAMES = re.compile(
    r"(^|/)(\.env(\..*)?|id_rsa|id_dsa|id_ecdsa|id_ed25519|credentials(\.json)?|secrets?\.(py|json|ya?ml))$"
    r"|\.(pem|key|p12|pfx)$",
    re.IGNORECASE,
)
_SECRET_CONTENT = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9_-]{20,}\b"
)
_MAX_COMPRESSION_RATIO = 200


class ArchiveRejected(ValueError):
    pass


@dataclass
class ArchiveContents:
    python_files: dict[str, str] = field(default_factory=dict)
    total_entries: int = 0
    excluded_sensitive: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _check_member_path(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", normalized):
        raise ArchiveRejected(f"Chemin dangereux refusé dans l'archive : {name}")
    return normalized


def _strip_common_root(files: dict[str, str]) -> dict[str, str]:
    """GitHub-style archives wrap everything in one folder, which would break import resolution."""
    while files:
        first_parts = {path.split("/", 1)[0] for path in files}
        if len(first_parts) != 1 or any("/" not in path for path in files):
            break
        # A folder holding __init__.py is a real package: its name is part of the imports.
        if f"{next(iter(first_parts))}/__init__.py" in files:
            break
        files = {path.split("/", 1)[1]: text for path, text in files.items()}
    return files


def read_python_archive(
    data: bytes,
    *,
    max_files: int,
    max_uncompressed_bytes: int,
    max_python_file_bytes: int,
) -> ArchiveContents:
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ArchiveRejected("Le fichier n'est pas une archive .zip valide.")

    contents = ArchiveContents()
    python_files: dict[str, str] = {}

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        contents.total_entries = len(infos)
        if len(infos) > max_files:
            raise ArchiveRejected(f"Trop de fichiers : {len(infos)} (maximum {max_files}).")

        declared_size = sum(info.file_size for info in infos)
        if declared_size > max_uncompressed_bytes:
            raise ArchiveRejected("Archive trop volumineuse une fois décompressée.")

        for info in infos:
            path = _check_member_path(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ArchiveRejected(f"Lien symbolique refusé dans l'archive : {info.filename}")
            if info.flag_bits & 0x1:
                raise ArchiveRejected(f"Fichier chiffré refusé dans l'archive : {info.filename}")
            if info.is_dir():
                continue
            if _SENSITIVE_NAMES.search(path):
                contents.excluded_sensitive.append(path)
                continue
            if not path.endswith(".py"):
                continue
            if info.file_size > max_python_file_bytes:
                contents.warnings.append(f"{path} : ignoré, fichier trop volumineux.")
                continue
            if info.compress_size and info.file_size / info.compress_size > _MAX_COMPRESSION_RATIO:
                raise ArchiveRejected(f"Taux de compression suspect : {info.filename}")

            # file_size comes from the archive and can lie, so the read itself is bounded.
            with archive.open(info) as member:
                raw = member.read(max_python_file_bytes + 1)
            if len(raw) > max_python_file_bytes:
                contents.warnings.append(f"{path} : ignoré, fichier trop volumineux.")
                continue
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                contents.warnings.append(f"{path} : ignoré, encodage non UTF-8.")
                continue
            if _SECRET_CONTENT.search(text):
                contents.excluded_sensitive.append(path)
                continue
            python_files[path] = text

    contents.python_files = _strip_common_root(python_files)
    return contents
