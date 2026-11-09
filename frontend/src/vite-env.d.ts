/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_FIREBASE_API_KEY: string;
  readonly VITE_FIREBASE_AUTH_DOMAIN: string;
  readonly VITE_FIREBASE_PROJECT_ID: string;
  readonly VITE_FIREBASE_APP_ID: string;
  readonly VITE_FIREBASE_AUTH_EMULATOR_HOST?: string;
  readonly VITE_CLOUD_REPOSITORY_STORAGE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
