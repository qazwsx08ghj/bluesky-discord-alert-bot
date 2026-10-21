// Light and dark follow the computer's setting unless the owner picks one in Settings.
// The choice is kept in this browser only.
import { useEffect, useState } from "react";

export type Theme = "system" | "light" | "dark";
const KEY = "alertbot-theme";

export function storedTheme(): Theme {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(storedTheme);
  useEffect(() => {
    applyTheme(theme);
    try {
      if (theme === "system") window.localStorage.removeItem(KEY);
      else window.localStorage.setItem(KEY, theme);
    } catch {
      // Private windows may refuse storage; the theme still applies until the page closes.
    }
  }, [theme]);
  return [theme, setTheme];
}
