"""Imports every model so SQLAlchemy and Alembic see the complete schema."""

from app.modules.analysis import models as analysis_models  # noqa: F401
from app.modules.audit import models as audit_models  # noqa: F401
from app.modules.auth import models as auth_models  # noqa: F401
from app.modules.code_analysis import models as code_models  # noqa: F401
from app.modules.ingestion import models as ingestion_models  # noqa: F401
from app.modules.projects import models as project_models  # noqa: F401
from app.modules.requirements import models as requirement_models  # noqa: F401
from app.modules.review import models as review_models  # noqa: F401
