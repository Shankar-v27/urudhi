/**
 * The selected data source, lifted to the app shell so every page reloads when it changes.
 *
 * Precedence when the app boots: `?source=` in the URL hash (deep links) → localStorage (`urudhi.source`) → `all`.
 * The selection is written back to both so a copied link and the next visit keep it.
 */

import { createContext, useContext } from "react";
import { DataSource, SOURCE_KEY, isDataSource, storageGet, storageSet } from "./api";

export interface SourceState {
  source: DataSource;
  setSource: (next: DataSource) => void;
}

export const SourceContext = createContext<SourceState>({ source: "all", setSource: () => {} });

export function useSource(): SourceState {
  return useContext(SourceContext);
}

/** `#/commitments/cmt_x?source=live_test` → "live_test"; absent or invalid → null. */
export function sourceFromHash(hash: string): DataSource | null {
  const q = hash.indexOf("?");
  if (q < 0) return null;
  const value = new URLSearchParams(hash.slice(q + 1)).get("source");
  return isDataSource(value) ? value : null;
}

export function storedSource(): DataSource {
  const value = storageGet(SOURCE_KEY);
  return isDataSource(value) ? value : "all";
}

export function rememberSource(source: DataSource): void {
  storageSet(SOURCE_KEY, source === "all" ? "" : source);
}
