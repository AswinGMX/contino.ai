/// <reference types="vite/client" />
interface ImportMetaEnv {
  /** Public URL of the backend API, e.g. https://contino-api.vercel.app. Empty = same origin (dev proxy). */
  readonly VITE_API_URL?: string;
}
interface ImportMeta { readonly env: ImportMetaEnv }
