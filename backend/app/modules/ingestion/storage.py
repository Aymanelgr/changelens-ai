from pathlib import Path

from app.core.config import get_settings


def save_file(project_id: int, kind: str, sha256: str, extension: str, data: bytes) -> str:
    """Stored under the content hash: nothing from the uploaded file name reaches the path."""
    folder = Path(get_settings().storage_dir) / str(project_id) / kind
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha256}{extension}"
    if not path.exists():
        path.write_bytes(data)
    return str(path)
