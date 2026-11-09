"""Firebase Admin initialization and request identity verification."""

import json
import os
from pathlib import Path
from typing import Annotated, Any, cast

import firebase_admin
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth, credentials
from firebase_admin.exceptions import FirebaseError
from google.auth.exceptions import DefaultCredentialsError
from pydantic import BaseModel, ConfigDict

bearer_scheme = HTTPBearer(auto_error=False)


class FirebasePrincipal(BaseModel):
    """The small, trusted identity projection used by application routes."""

    model_config = ConfigDict(extra="ignore")

    uid: str
    email: str | None = None
    email_verified: bool
    display_name: str | None = None


def _get_firebase_app() -> firebase_admin.App:
    """Initialize Firebase Admin only when a protected route is used."""
    project_id = os.getenv("FIREBASE_PROJECT_ID")
    if not project_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firebase is not configured. Set FIREBASE_PROJECT_ID and Admin credentials.",
        )

    emulator_host = os.getenv("FIREBASE_AUTH_EMULATOR_HOST")
    app_env = os.getenv("APP_ENV", "production").strip().lower()
    if emulator_host and app_env not in {"development", "test"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The Firebase Auth Emulator is allowed only in development or test.",
        )

    try:
        app = firebase_admin.get_app()
        if app.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Firebase Admin is already initialized for a different project.",
            )
        return app
    except ValueError:
        pass

    credential_json = os.getenv("FIREBASE_ADMIN_CREDENTIALS_JSON")
    credential_path = os.getenv("FIREBASE_ADMIN_CREDENTIALS_PATH") or os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS"
    )

    try:
        if emulator_host:
            # Firebase Admin recognizes FIREBASE_AUTH_EMULATOR_HOST and accepts
            # emulator-issued tokens. A project ID is still required.
            return firebase_admin.initialize_app(options={"projectId": project_id})

        if credential_json:
            try:
                credential_data = json.loads(credential_json)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="The configured Firebase Admin credentials are invalid.",
                ) from exc
            if not isinstance(credential_data, dict):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="The configured Firebase Admin credentials are invalid.",
                )
            return firebase_admin.initialize_app(
                credentials.Certificate(credential_data), options={"projectId": project_id}
            )

        if credential_path:
            path = Path(credential_path).expanduser()
            if not path.is_file():
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="The configured Firebase Admin credential file was not found.",
                )
            return firebase_admin.initialize_app(
                credentials.Certificate(str(path)), options={"projectId": project_id}
            )

        # Application Default Credentials are appropriate on supported cloud
        # runtimes. Local developers can provide a credential path above.
        return firebase_admin.initialize_app(
            credentials.ApplicationDefault(), options={"projectId": project_id}
        )
    except HTTPException:
        raise
    except (DefaultCredentialsError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firebase Admin credentials could not be initialized.",
        ) from exc


def _verify_id_token(token: str, app: firebase_admin.App) -> dict[str, Any]:
    """Verify the bearer token and reject revoked or invalid sessions."""
    return cast(dict[str, Any], auth.verify_id_token(token, app=app, check_revoked=True))


def get_current_user(
    credentials_header: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> FirebasePrincipal:
    """Resolve the request identity from a verified Firebase ID token."""
    if credentials_header is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A Firebase bearer token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    app = _get_firebase_app()
    try:
        claims = _verify_id_token(credentials_header.credentials, app)
    except (auth.InvalidIdTokenError, auth.ExpiredIdTokenError, auth.RevokedIdTokenError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The Firebase ID token is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except FirebaseError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firebase token verification is temporarily unavailable.",
        ) from exc

    principal = FirebasePrincipal(
        uid=str(claims["uid"]),
        email=claims.get("email"),
        email_verified=bool(claims.get("email_verified", False)),
        display_name=claims.get("name"),
    )
    if not principal.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify your email address before using the application.",
        )
    return principal
