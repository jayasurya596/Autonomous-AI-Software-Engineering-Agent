import axios from "axios";
import { firebaseAuth } from "../lib/firebase";

export const PREVIEW_BACKEND_UNAVAILABLE_MESSAGE =
  "Agent services are not included in this free Firebase preview.";

const isFirebasePreviewOnly =
  import.meta.env.VITE_FIREBASE_PREVIEW_ONLY?.trim().toLowerCase() === "true";

export const api = axios.create({
  baseURL: "/api/v1",
  timeout: 10_000,
});

api.interceptors.request.use(async (request) => {
  if (isFirebasePreviewOnly) {
    throw new Error(PREVIEW_BACKEND_UNAVAILABLE_MESSAGE);
  }
  const token = await firebaseAuth?.currentUser?.getIdToken();
  if (token) request.headers.Authorization = `Bearer ${token}`;
  return request;
});
