import hashlib
from datetime import datetime
from pathlib import PurePath

from fastapi import APIRouter, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.deps import CAN_IMPORT, CurrentUser, DbSession, require_role
from app.core.enums import ExtractionStatus
from app.modules.analysis.staleness import mark_stale_for_document, mark_stale_for_snapshot
from app.modules.audit.service import record_event
from app.modules.code_analysis.archive import ArchiveRejected
from app.modules.code_analysis.models import RepositorySnapshot
from app.modules.code_analysis.service import create_snapshot
from app.modules.extraction.pdf import PdfRejected, extract_pdf
from app.modules.extraction.segmenter import segment_markdown, segment_text
from app.modules.ingestion.models import Document, DocumentVersion, Segment
from app.modules.ingestion.storage import save_file
from app.modules.ingestion.validation import DocumentRejected, safe_filename, validate_document

router = APIRouter(tags=["ingestion"])


class DocumentVersionOut(BaseModel):
    document_id: int
    version_id: int
    name: str
    number: int
    sha256: str
    extraction_status: ExtractionStatus
    file_type: str
    page_count: int | None
    segment_count: int
    already_imported: bool = False
    message: str | None = None


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    archive_name: str
    sha256: str
    file_count: int
    python_file_count: int
    parse_error_count: int
    unresolved_call_count: int
    excluded_sensitive_count: int
    warnings: list[str]
    created_at: datetime
    already_imported: bool = False


def _read_limited(file: UploadFile, limit: int) -> bytes:
    """Reads one byte past the limit: enough to detect an oversized file without loading it."""
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Fichier trop volumineux (maximum {limit // (1024 * 1024)} Mo).",
        )
    return data


def _version_out(version: DocumentVersion, name: str, count: int, **extra) -> DocumentVersionOut:
    return DocumentVersionOut(
        document_id=version.document_id,
        version_id=version.id,
        name=name,
        number=version.number,
        sha256=version.sha256,
        extraction_status=version.extraction_status,
        file_type=version.file_type,
        page_count=version.page_count,
        segment_count=count,
        **extra,
    )


@router.post("/projects/{project_id}/documents", response_model=DocumentVersionOut)
def upload_document(project_id: int, file: UploadFile, db: DbSession, user: CurrentUser):
    require_role(db, user, project_id, *CAN_IMPORT)
    settings = get_settings()
    data = _read_limited(file, settings.max_document_bytes)
    try:
        name = safe_filename(file.filename)
        extension, text = validate_document(name, data, settings.max_document_bytes)
    except DocumentRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    digest = hashlib.sha256(data).hexdigest()
    document = db.scalar(
        select(Document).where(Document.project_id == project_id, Document.name == name)
    )
    latest = None
    if document:
        latest = db.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.number.desc())
        )
    if latest and latest.sha256 == digest:
        count = db.scalar(
            select(func.count())
            .select_from(Segment)
            .where(Segment.document_version_id == latest.id)
        )
        return _version_out(latest, name, count, already_imported=True, message="Déjà importé.")

    if document is None:
        document = Document(project_id=project_id, name=name)
        db.add(document)
        db.flush()

    page_count = None
    extraction_status = ExtractionStatus.EXTRACTED
    if extension == ".pdf":
        try:
            pdf = extract_pdf(data, settings.max_pdf_pages)
        except PdfRejected as exc:
            db.rollback()
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        segments = pdf.segments
        page_count = pdf.page_count
        if pdf.needs_ocr:
            extraction_status = ExtractionStatus.NEEDS_OCR
    elif extension == ".md":
        segments = segment_markdown(text)
    else:
        segments = segment_text(text)

    version = DocumentVersion(
        document_id=document.id,
        number=(latest.number + 1) if latest else 1,
        sha256=digest,
        file_type=extension.lstrip("."),
        page_count=page_count,
        extraction_status=extraction_status,
        storage_path=save_file(project_id, "documents", digest, extension, data),
    )
    db.add(version)
    db.flush()
    db.add_all(
        Segment(
            document_version_id=version.id,
            page=getattr(segment, "page", None),
            section_path=segment.section_path,
            start_char=segment.start_char,
            end_char=segment.end_char,
            text=segment.text,
            sha256=segment.sha256,
        )
        for segment in segments
    )
    stale = mark_stale_for_document(db, document.id, version.id) if latest else 0
    record_event(
        db,
        "DOCUMENT_UPLOADED",
        project_id=project_id,
        actor_id=user.id,
        entity_type="document_version",
        entity_id=version.id,
        name=name,
        number=version.number,
        sha256=digest,
        findings_marked_stale=stale,
    )
    db.commit()
    return _version_out(version, name, len(segments))


@router.get("/projects/{project_id}/documents", response_model=list[DocumentVersionOut])
def list_documents(project_id: int, db: DbSession, user: CurrentUser):
    require_role(db, user, project_id)
    rows = db.execute(
        select(DocumentVersion, Document.name, func.count(Segment.id))
        .join(Document, Document.id == DocumentVersion.document_id)
        .outerjoin(Segment, Segment.document_version_id == DocumentVersion.id)
        .where(Document.project_id == project_id)
        .group_by(DocumentVersion.id, Document.name)
        .order_by(Document.name, DocumentVersion.number)
    ).all()
    return [_version_out(version, name, count) for version, name, count in rows]


@router.post("/projects/{project_id}/repository-snapshots", response_model=SnapshotOut)
def upload_repository(project_id: int, file: UploadFile, db: DbSession, user: CurrentUser):
    require_role(db, user, project_id, *CAN_IMPORT)
    data = _read_limited(file, get_settings().max_archive_bytes)
    name = PurePath((file.filename or "archive.zip").replace("\\", "/")).name
    if not name.lower().endswith(".zip"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Une archive .zip est attendue.")

    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(RepositorySnapshot).where(
            RepositorySnapshot.project_id == project_id, RepositorySnapshot.sha256 == digest
        )
    )
    if existing:
        out = SnapshotOut.model_validate(existing)
        out.already_imported = True
        return out

    try:
        snapshot = create_snapshot(
            db,
            project_id=project_id,
            archive_name=name[:255],
            sha256=digest,
            storage_path=save_file(project_id, "repositories", digest, ".zip", data),
            data=data,
        )
    except ArchiveRejected as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    stale = mark_stale_for_snapshot(db, project_id, snapshot.id)
    record_event(
        db,
        "REPOSITORY_UPLOADED",
        project_id=project_id,
        actor_id=user.id,
        entity_type="repository_snapshot",
        entity_id=snapshot.id,
        name=name,
        sha256=digest,
        findings_marked_stale=stale,
    )
    db.commit()
    return snapshot


@router.get("/projects/{project_id}/repository-snapshots", response_model=list[SnapshotOut])
def list_repositories(project_id: int, db: DbSession, user: CurrentUser):
    require_role(db, user, project_id)
    return db.scalars(
        select(RepositorySnapshot)
        .where(RepositorySnapshot.project_id == project_id)
        .order_by(RepositorySnapshot.id)
    ).all()
