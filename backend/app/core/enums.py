"""Domain vocabularies from the specification, in one place to avoid circular imports."""

from enum import StrEnum


class ProjectRole(StrEnum):
    ADMIN = "ADMIN"
    ANALYST = "ANALYST"
    REVIEWER = "REVIEWER"
    VIEWER = "VIEWER"


class ExtractionStatus(StrEnum):
    EXTRACTED = "EXTRACTED"
    NEEDS_OCR = "NEEDS_OCR"


class SymbolKind(StrEnum):
    MODULE = "MODULE"
    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"


class ChangeRequestStatus(StrEnum):
    DRAFT = "DRAFT"
    ANALYSIS_IN_PROGRESS = "ANALYSIS_IN_PROGRESS"
    IN_REVIEW = "IN_REVIEW"
    REVIEW_COMPLETED = "REVIEW_COMPLETED"
    CLOSED = "CLOSED"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RunInputType(StrEnum):
    REQUIREMENT_SET_BEFORE = "REQUIREMENT_SET_BEFORE"
    REQUIREMENT_SET_AFTER = "REQUIREMENT_SET_AFTER"
    DOCUMENT_VERSION = "DOCUMENT_VERSION"
    REPOSITORY_SNAPSHOT = "REPOSITORY_SNAPSHOT"


class FindingStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    CORRECTED = "CORRECTED"
    SUPERSEDED = "SUPERSEDED"


class TargetType(StrEnum):
    CODE_SYMBOL = "CODE_SYMBOL"
    DOCUMENT_SECTION = "DOCUMENT_SECTION"
    REQUIREMENT = "REQUIREMENT"


class ImpactType(StrEnum):
    OUTDATED_VALUE = "OUTDATED_VALUE"
    LOGIC_REVIEW = "LOGIC_REVIEW"
    TEST_UPDATE = "TEST_UPDATE"
    DOC_UPDATE = "DOC_UPDATE"
    DEPENDENT_COMPONENT = "DEPENDENT_COMPONENT"
    INCONSISTENCY = "INCONSISTENCY"
    NONE = "NONE"


class LinkType(StrEnum):
    EXPLICIT = "EXPLICIT"
    STATIC = "STATIC"
    INFERRED = "INFERRED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class LlmVerdict(StrEnum):
    LIKELY_IMPACTED = "LIKELY_IMPACTED"
    NOT_IMPACTED = "NOT_IMPACTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class Support(StrEnum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"


class EvidenceRole(StrEnum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"


class EvidenceSource(StrEnum):
    SEGMENT = "SEGMENT"
    SYMBOL = "SYMBOL"
    REQUIREMENT = "REQUIREMENT"


class TestOrigin(StrEnum):
    RULE = "RULE"
    LLM = "LLM"


class TestStatus(StrEnum):
    # AI-007: a suggested test has exactly one possible status.
    SUGGESTED = "SUGGESTED"


class Decision(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    CORRECT = "CORRECT"


class TraceLinkStatus(StrEnum):
    SUGGESTED = "SUGGESTED"
    CONFIRMED = "CONFIRMED"
    REVOKED = "REVOKED"
