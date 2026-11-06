"""full-text search, embeddings, LLM results and the controlled audit purge

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_SEGMENT_TSV = "to_tsvector('french', coalesce(section_path, '') || ' ' || text)"
_SYMBOL_TSV = (
    "to_tsvector('french', replace(qualified_name || ' ' || file_path || ' ' "
    "|| coalesce(docstring, '') || ' ' || source_text, '_', ' '))"
)
_VERDICTS = "'LIKELY_IMPACTED', 'NOT_IMPACTED', 'INSUFFICIENT_EVIDENCE'"
_IMPACTS = (
    "'OUTDATED_VALUE', 'LOGIC_REVIEW', 'TEST_UPDATE', 'DOC_UPDATE', "
    "'DEPENDENT_COMPONENT', 'INCONSISTENCY', 'NONE'"
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column(
        "segment",
        sa.Column("tsv", postgresql.TSVECTOR(), sa.Computed(_SEGMENT_TSV, persisted=True)),
    )
    op.add_column("segment", sa.Column("embedding", Vector(), nullable=True))
    op.add_column("segment", sa.Column("embedding_model", sa.String(200), nullable=True))
    op.create_index("ix_segment_tsv", "segment", ["tsv"], postgresql_using="gin")

    op.add_column(
        "code_symbol",
        sa.Column("tsv", postgresql.TSVECTOR(), sa.Computed(_SYMBOL_TSV, persisted=True)),
    )
    op.add_column("code_symbol", sa.Column("embedding", Vector(), nullable=True))
    op.add_column("code_symbol", sa.Column("embedding_model", sa.String(200), nullable=True))
    op.add_column("code_symbol", sa.Column("embedding_sha256", sa.String(64), nullable=True))
    op.create_index("ix_code_symbol_tsv", "code_symbol", ["tsv"], postgresql_using="gin")

    op.add_column(
        "analysis_run",
        sa.Column("progress", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.alter_column("analysis_run", "progress", server_default=None)

    op.add_column(
        "finding_occurrence",
        sa.Column("llm_analyzed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("finding_occurrence", "llm_analyzed", server_default=None)
    op.add_column("finding_occurrence", sa.Column("llm_model", sa.String(200), nullable=True))
    op.add_column("finding_occurrence", sa.Column("llm_verdict", sa.String(40), nullable=True))
    op.add_column("finding_occurrence", sa.Column("llm_explanation", sa.Text(), nullable=True))
    op.add_column("finding_occurrence", sa.Column("llm_impact_type", sa.String(40), nullable=True))
    op.add_column(
        "finding_occurrence",
        sa.Column(
            "llm_uncertainties", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
        ),
    )
    op.alter_column("finding_occurrence", "llm_uncertainties", server_default=None)
    op.create_check_constraint(
        "llmverdict", "finding_occurrence", f"llm_verdict IN ({_VERDICTS})"
    )
    op.create_check_constraint(
        "llm_impact_type", "finding_occurrence", f"llm_impact_type IN ({_IMPACTS})"
    )

    op.add_column(
        "evidence", sa.Column("from_llm", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.alter_column("evidence", "from_llm", server_default=None)

    # FR-018 keeps only a minimal event when a project is deleted, which the trigger from 0001
    # refused outright. A delete now passes in one case: the same transaction has declared,
    # with SET LOCAL, that it is purging that very project. No API route can reach this except
    # project deletion, and every other UPDATE or DELETE is still refused.
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_event_is_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND OLD.project_id IS NOT NULL
               AND current_setting('changelens.audit_purge_project', true)
                   = OLD.project_id::text THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'audit_event is append-only: % refused', TG_OP;
        END;
        $$ LANGUAGE plpgsql
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_event_is_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_event is append-only: % refused', TG_OP;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.drop_column("evidence", "from_llm")
    for name in ("llm_impact_type", "llmverdict"):
        op.drop_constraint(name, "finding_occurrence", type_="check")
    for column in (
        "llm_uncertainties",
        "llm_impact_type",
        "llm_explanation",
        "llm_verdict",
        "llm_model",
        "llm_analyzed",
    ):
        op.drop_column("finding_occurrence", column)
    op.drop_column("analysis_run", "progress")
    op.drop_index("ix_code_symbol_tsv", table_name="code_symbol")
    for column in ("embedding_sha256", "embedding_model", "embedding", "tsv"):
        op.drop_column("code_symbol", column)
    op.drop_index("ix_segment_tsv", table_name="segment")
    for column in ("embedding_model", "embedding", "tsv"):
        op.drop_column("segment", column)
