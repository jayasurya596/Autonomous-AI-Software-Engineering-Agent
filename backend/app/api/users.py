"""User profile endpoints backed by Firestore."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from google.api_core.exceptions import GoogleAPICallError

from app.core.auth import FirebasePrincipal, get_current_user
from app.core.firestore import get_firestore_client
from app.repositories import firestore as repository
from app.schemas.profile import UserProfileInput, UserProfileResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/profile", response_model=UserProfileResponse)
def read_profile(
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
) -> UserProfileResponse:
    try:
        profile = repository.get_profile(get_firestore_client(), principal.uid)
    except GoogleAPICallError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firestore is temporarily unavailable.",
        ) from exc
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found.")
    return UserProfileResponse.model_validate(profile)


@router.put("/profile", response_model=UserProfileResponse)
def write_profile(
    profile: UserProfileInput,
    principal: Annotated[FirebasePrincipal, Depends(get_current_user)],
) -> UserProfileResponse:
    if not principal.email or len(principal.email) > 254:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A verified email is required to save the onboarding profile.",
        )
    try:
        saved = repository.save_profile(
            get_firestore_client(), principal.uid, principal.email, profile
        )
    except GoogleAPICallError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firestore is temporarily unavailable.",
        ) from exc
    return UserProfileResponse.model_validate(saved)
