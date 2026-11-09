import { getApp, getApps, initializeApp } from "firebase/app";
import { connectAuthEmulator, getAuth, type Auth } from "firebase/auth";

const config = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

export const isFirebaseConfigured = Object.values(config).every(
  (value) => typeof value === "string" && value.trim().length > 0,
);

export const isGoogleAuthEnabled =
  import.meta.env.VITE_FIREBASE_GOOGLE_AUTH_ENABLED?.trim().toLowerCase() !== "false";

let authClient: Auth | null = null;

if (isFirebaseConfigured) {
  const app = getApps().length > 0 ? getApp() : initializeApp(config);
  authClient = getAuth(app);

  const emulatorHost = import.meta.env.VITE_FIREBASE_AUTH_EMULATOR_HOST?.trim();
  if (emulatorHost) {
    const emulatorUrl = emulatorHost.startsWith("http")
      ? emulatorHost
      : `http://${emulatorHost}`;
    connectAuthEmulator(authClient, emulatorUrl, { disableWarnings: true });
  }
}

export const firebaseAuth = authClient;
