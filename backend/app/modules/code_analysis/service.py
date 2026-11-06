from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.code_analysis.analyzer import SymbolInfo, analyze_repository
from app.modules.code_analysis.archive import read_python_archive
from app.modules.code_analysis.models import CallEdge, CodeSymbol, RepositorySnapshot


def create_snapshot(
    db: Session,
    *,
    project_id: int,
    archive_name: str,
    sha256: str,
    storage_path: str,
    data: bytes,
) -> RepositorySnapshot:
    """Reads and analyzes the archive. Raises ArchiveRejected for a refused file."""
    settings = get_settings()
    contents = read_python_archive(
        data,
        max_files=settings.max_archive_files,
        max_uncompressed_bytes=settings.max_archive_uncompressed_bytes,
        max_python_file_bytes=settings.max_python_file_bytes,
    )
    analysis = analyze_repository(contents.python_files)

    warnings = contents.warnings + analysis.parse_errors
    warnings += [f"{path} : exclu, contenu sensible." for path in contents.excluded_sensitive]
    snapshot = RepositorySnapshot(
        project_id=project_id,
        sha256=sha256,
        archive_name=archive_name,
        storage_path=storage_path,
        file_count=contents.total_entries,
        python_file_count=len(contents.python_files),
        parse_error_count=len(analysis.parse_errors),
        unresolved_call_count=sum(
            1 for call in analysis.calls if not call.resolved and not call.external
        ),
        excluded_sensitive_count=len(contents.excluded_sensitive),
        warnings=warnings,
    )
    db.add(snapshot)
    db.flush()

    rows: dict[int, CodeSymbol] = {}

    def to_row(symbol: SymbolInfo) -> CodeSymbol:
        return CodeSymbol(
            snapshot_id=snapshot.id,
            file_path=symbol.file_path,
            qualified_name=symbol.qualified_name,
            kind=symbol.kind,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            signature=symbol.signature,
            docstring=symbol.docstring,
            is_test=symbol.is_test,
            source_text=symbol.source_text,
            number_literals=symbol.number_literals,
            text_items=symbol.text_items,
        )

    for symbol in analysis.symbols:
        rows[id(symbol)] = to_row(symbol)
    db.add_all(rows.values())
    db.flush()  # assigns primary keys, which the call edges need

    db.add_all(
        CallEdge(
            snapshot_id=snapshot.id,
            caller_id=rows[id(call.caller)].id,
            callee_id=rows[id(call.callee)].id if call.callee else None,
            callee_name=call.callee_name,
            line=call.line,
            resolved=call.resolved,
            external=call.external,
        )
        for call in analysis.calls
    )
    return snapshot
