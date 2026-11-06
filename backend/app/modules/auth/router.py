from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.deps import CurrentUser, DbSession
from app.core.security import create_access_token, hash_password, verify_password
from app.modules.audit.service import record_event
from app.modules.auth.models import User

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+$")
    password: str = Field(min_length=8, max_length=128)
    is_admin: bool = False


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    is_admin: bool


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, db: DbSession):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not verify_password(body.password, user.password_hash if user else None):
        # Same message whether the email exists or not.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants incorrects.")
    return TokenOut(access_token=create_access_token(user.id))


@router.get("/users/me", response_model=UserOut)
def read_me(user: CurrentUser):
    return user


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: DbSession, user: CurrentUser):
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Réservé aux administrateurs.")
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Cet email existe déjà.")
    created = User(email=email, password_hash=hash_password(body.password), is_admin=body.is_admin)
    db.add(created)
    db.flush()
    record_event(
        db,
        "USER_CREATED",
        project_id=None,
        actor_id=user.id,
        entity_type="user",
        entity_id=created.id,
    )
    db.commit()
    return created


def bootstrap_admin() -> None:
    """Creates the first administrator. Does nothing once any user exists."""
    settings = get_settings()
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(User)):
            return
        db.add(
            User(
                email=settings.bootstrap_admin_email.strip().lower(),
                password_hash=hash_password(settings.bootstrap_admin_password),
                is_admin=True,
            )
        )
        db.commit()
