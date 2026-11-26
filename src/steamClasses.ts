// Steam class lookups that work on both Decky loaders.
//
// Decky 3.3.1 runs on the webpack Steam client with @decky/ui 4; Decky 4.0 runs
// on the ES-module client with @decky/ui 5, and that client renamed nearly every
// CSS class. A class-map key ("BasicFooter") keeps its name; only the value (the
// class Steam actually renders) changes. @decky/ui is supplied by the loader at
// runtime, so its class maps are probed, never assumed: a missing map only means
// fewer selectors, never an error.
import * as DFL from "@decky/ui";

// Named class modules both @decky/ui 4 and 5 export (5 fills them per Steam build).
const NAMED_CLASS_MODULES = [
  "footerClasses",
  "gamepadDialogClasses",
  "gamepadUIClasses",
  "mainMenuAppRunningClasses",
  "quickAccessMenuClasses",
];

const valueCache = new Map<string, string[]>();
let warned = false;

function warnOnce(error: unknown) {
  if (warned) return;
  warned = true;
  try { console.warn("[Awesome Mix] Steam class maps unavailable; using readable class names only", error); } catch { /* console may be gone */ }
}

export function cssEscapeClass(value: string): string {
  try {
    if (typeof CSS !== "undefined" && typeof CSS.escape === "function") return CSS.escape(value);
  } catch { /* fall through */ }
  const lead = /^-?\d/.test(value) ? (value[0] === "-" ? 1 : 0) : -1;
  return Array.from(value, (c, i) => (i === lead ? `\\3${c} ` : /[\w-]/.test(c) ? c : `\\${c}`)).join("");
}

/** Class strings Steam renders for a class-map key, from every class map the loader offers. */
export function steamClassValues(name: string): string[] {
  const cached = valueCache.get(name);
  if (cached) return cached;
  const values: string[] = [];
  const add = (value: unknown) => {
    if (typeof value !== "string") return;
    const normalized = value.trim().split(/\s+/).filter(Boolean).join(" ");
    if (normalized && !values.includes(normalized)) values.push(normalized);
  };
  try {
    const dfl = DFL as any;
    for (const moduleName of NAMED_CLASS_MODULES) {
      try { add(dfl?.[moduleName]?.[name]); } catch { /* a getter may throw */ }
    }
    const maps = dfl?.classMap;
    if (Array.isArray(maps)) for (const map of maps) {
      try { add(map?.[name]); } catch { /* skip a broken map */ }
    }
  } catch (error) {
    warnOnce(error);
  }
  valueCache.set(name, values);
  return values;
}

/**
 * CSS selectors for Steam elements known by class-map key: the class-map value
 * (current Steam) first, then (unless includeReadable is false) the readable-name
 * match older builds rendered.
 */
export function steamClassSelectors(names: readonly string[], includeReadable = true): string[] {
  const selectors: string[] = [];
  const add = (selector: string) => { if (!selectors.includes(selector)) selectors.push(selector); };
  for (const name of names) {
    for (const value of steamClassValues(name)) add(value.split(" ").map((c) => `.${cssEscapeClass(c)}`).join(""));
  }
  if (includeReadable) for (const name of names) add(`[class*="${name}"]`);
  return selectors;
}

/** Selector list with each entry prefixed by every scope, joined for a stylesheet. */
export function scopedSelectorList(scopes: readonly string[], selectors: readonly string[]): string {
  return scopes.flatMap((scope) => selectors.map((selector) => `${scope} ${selector}`)).join(",\n");
}

/** Testing only: forget cached lookups. */
export function resetSteamClassCache() {
  valueCache.clear();
  warned = false;
}
