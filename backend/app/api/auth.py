"""Authentication routes backed by Firebase ID-token verification."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.auth import FirebasePrincipal, get_current_user

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get("/me", response_model=FirebasePrincipal)
def get_authenticated_user(
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
) -> FirebasePrincipal:
    """Return identity claims from the verified Firebase ID token."""
    return principal
