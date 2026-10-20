/// <reference types="vite/client" />

import type { BiescapeApi } from "../electron/preload";

declare global {
  interface Window {
    biescape: BiescapeApi;
  }
}

export {};
