"""Firestore client initialization for the local emulator or Firebase project."""

import os
from typing import Any

from fastapi import HTTPException, status
from firebase_admin import firestore
from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore as google_firestore

from app.core.auth import _get_firebase_app


def get_firestore_client() -> Any:
    """Return the privileged server client after enforcing emulator policy."""
    emulator_host = os.getenv("FIRESTORE_EMULATOR_HOST")
    app_env = os.getenv("APP_ENV", "production").strip().lower()
    if emulator_host and app_env not in {"development", "test"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The Firestore Emulator is allowed only in development or test.",
        )

    try:
        if emulator_host:
            project_id = os.getenv("FIREBASE_PROJECT_ID")
            if not project_id:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Firestore is not configured for this Firebase project.",
                )
            return google_firestore.Client(
                project=project_id,
                credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
            )
        return firestore.client(app=_get_firebase_app())
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firestore is not configured for this Firebase project.",
        ) from exc
