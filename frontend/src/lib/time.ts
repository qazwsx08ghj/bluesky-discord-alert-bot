import { useEffect, useState } from "react";

const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const clock = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const full = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

const STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
];

/** "just now", "3 minutes ago", "in 2 minutes", "yesterday". */
export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  if (Math.abs(seconds) < 45) return seconds > 0 ? "in a moment" : "just now";
  for (const [unit, size] of STEPS) {
    if (Math.abs(seconds) >= size || unit === "minute") {
      return relative.format(Math.round(seconds / size), unit);
    }
  }
  return "";
}

/** Short "Next check in 3 minutes" style text for a future time. */
export function untilText(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const seconds = (new Date(iso).getTime() - now) / 1000;
  if (seconds <= 30) return "in a moment";
  return ago(iso, now);
}

export function clockTime(iso: string): string {
  return clock.format(new Date(iso));
}

export function fullTime(iso: string | null | undefined): string {
  return iso ? full.format(new Date(iso)) : "";
}

/** Re-renders every 30 seconds so "2 minutes ago" stays true. */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function plural(n: number, word: string, many = word + "s"): string {
  return `${n} ${n === 1 ? word : many}`;
}
