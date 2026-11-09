import { FirebaseError } from "firebase/app";
import { PREVIEW_BACKEND_UNAVAILABLE_MESSAGE } from "../api/client";

const messages: Record<string, string> = {
  "auth/email-already-in-use": "An account already exists for this email.",
  "auth/invalid-credential": "The email or password is incorrect.",
  "auth/invalid-email": "Enter a valid email address.",
  "auth/network-request-failed": "Firebase could not be reached. Check your connection.",
  "auth/popup-closed-by-user": "The sign-in window was closed before finishing.",
  "auth/too-many-requests": "Too many attempts. Wait a little and try again.",
  "auth/weak-password": "Choose a stronger password with at least 8 characters.",
  "auth/unauthorized-domain": "This site is not authorized in the Firebase project.",
  "auth/user-disabled": "This account has been disabled.",
};

export function authErrorMessage(error: unknown): string {
  if (error instanceof FirebaseError) {
    return messages[error.code] ?? "Firebase could not complete the request. Try again.";
  }
  if (error instanceof Error && error.message === "Firebase is not configured.") {
    return "Firebase is not configured. Add the web app values to the root .env file.";
  }
  if (error instanceof Error && error.message === PREVIEW_BACKEND_UNAVAILABLE_MESSAGE) {
    return error.message;
  }
  return "Something went wrong. Try again.";
}
