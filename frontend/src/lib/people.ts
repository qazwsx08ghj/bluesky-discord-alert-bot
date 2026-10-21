import type { Account } from "../api/types";

// Muted colours for initials avatars, all readable with white text (4.5:1 or better).
const COLORS = ["#B5386A", "#1F7A63", "#9A5B12", "#2F6C99", "#5B4BC4", "#7A4F9E", "#3D6B2B", "#A3462B"];

export function avatarColor(handle: string): string {
  let hash = 0;
  for (const ch of handle) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return COLORS[hash % COLORS.length];
}

export function initials(name: string): string {
  const words = name.replace(/^@/, "").split(/[\s._-]+/).filter(Boolean);
  return words.slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
}

export function displayName(account: Pick<Account, "display_name" | "handle">): string {
  return account.display_name?.trim() || "@" + account.handle;
}

export function profileUrl(handle: string): string {
  return `https://bsky.app/profile/${encodeURIComponent(handle.replace(/^@/, ""))}`;
}

/** Only https links to Bluesky's own sites are ever rendered as links. */
export function safeBlueskyLink(url: unknown): string | null {
  if (typeof url !== "string") return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && parsed.hostname === "bsky.app" ? parsed.href : null;
  } catch {
    return null;
  }
}
