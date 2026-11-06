import type { MessageKey } from "./i18n";
import { hasMessage } from "./i18n";

export const FREQUENCIES = [15, 60, 360, 720, 1440, 10080] as const;

export function frequencyLabel(minutes: number, t: (k: MessageKey) => string): string {
  const key = `freq.${minutes}`;
  if (hasMessage(key)) return t(key);
  return minutes % 60 === 0 ? `${minutes / 60} h` : `${minutes} min`;
}

export function splitList(s: string): string[] {
  return s.split(/[,،\n]/).map((x) => x.trim()).filter(Boolean);
}

export function deviceLabel(ua: string | null): string | null {
  if (!ua) return null;
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox"
    : /Safari\//.test(ua) ? "Safari" : null;
  const os = /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS"
    : /Mac OS X/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : null;
  return [browser, os].filter(Boolean).join(" · ") || ua.slice(0, 60);
}
