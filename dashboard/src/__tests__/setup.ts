import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Node ≥ 22 ships an experimental `localStorage` global that can shadow jsdom's Storage with an object
// lacking `clear`/`getItem`. The app only needs the Storage interface, so install a small in-memory one.
function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() { return map.size; },
    clear: () => map.clear(),
    getItem: (key: string) => (map.has(key) ? map.get(key)! : null),
    key: (index: number) => Array.from(map.keys())[index] ?? null,
    removeItem: (key: string) => { map.delete(key); },
    setItem: (key: string, value: string) => { map.set(key, String(value)); },
  };
}

if (typeof window.localStorage?.clear !== "function" || typeof window.localStorage?.getItem !== "function") {
  Object.defineProperty(window, "localStorage", { value: memoryStorage(), configurable: true });
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  window.location.hash = "";
});
