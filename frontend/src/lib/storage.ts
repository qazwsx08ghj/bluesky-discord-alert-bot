// Small per-browser memories (never secrets): a skipped setup step, a dismissed result.
export function remember(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem("alertbot-" + key);
    else window.localStorage.setItem("alertbot-" + key, value);
  } catch {
    // Storage may be unavailable; the panel works without it.
  }
}

export function recall(key: string): string | null {
  try {
    return window.localStorage.getItem("alertbot-" + key);
  } catch {
    return null;
  }
}
