import type { Fix } from "../api/types";

/** Where a problem's fix is made, and what the link says. */
export function fixLink(fix: Fix | string | undefined): { to: string; label: string } | null {
  switch (fix) {
    case "update_login":
      return { to: "/settings#bluesky", label: "Update the Bluesky login" };
    case "update_webhook":
      return { to: "/settings#channels", label: "Check the Discord channel" };
    case "choose_channel":
      return { to: "/settings#channels", label: "Choose a default channel" };
    case "check_handle":
      return { to: "/accounts", label: "Check the account" };
    case "add_account":
      return { to: "/accounts?add=1", label: "Add an account" };
    default:
      return null;
  }
}
