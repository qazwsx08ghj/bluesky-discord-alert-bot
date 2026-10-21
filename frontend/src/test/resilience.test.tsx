import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { ApiError, api, onThisSite } from "../api/client";
import { TOKEN, workingState } from "./fakeApi";
import { changes, renderApp } from "./render";
import { server } from "./server";

describe("when the bot can't be reached", () => {
  it("says so in plain words, dims the page, and recovers by itself", async () => {
    const state = workingState();
    const { client } = renderApp("/accounts", state);
    await screen.findByText("Mira Draws", { selector: "b" });
    state.offline = true;
    await client.refetchQueries({ queryKey: ["status"] }); // instead of waiting for the next poll
    await screen.findByText("Can't reach the bot", { selector: ".banner b" });
    expect(screen.getByText(/Make sure Docker Desktop is open/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Bot status: Not running/ })).toBeInTheDocument();
    expect(document.querySelector(".app")).toHaveClass("offline");
    state.offline = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.queryByText("Can't reach the bot", { selector: ".banner b" })).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Bot status: Running/ })).toBeInTheDocument();
  });

  it("explains a stopped checker and what to do about it", async () => {
    const state = workingState();
    state.checkerHealthy = false;
    const { user } = renderApp("/accounts", state);
    expect((await screen.findAllByText("The checker isn't running.")).length).toBeGreaterThan(0);
    await user.click(await screen.findByRole("button", { name: "What to do" }));
    const dialog = screen.getByRole("dialog", { name: /The checker isn't running/ });
    expect(within(dialog).getByText(/Open Docker Desktop and run start.bat/)).toBeInTheDocument();
  });

  it("shows a problem with a link to its fix", async () => {
    const state = workingState();
    state.loginState = "failed";
    const { user } = renderApp("/accounts", state);
    // In the banner, and as the reason in the sidebar status.
    expect(await screen.findAllByText("Bluesky rejected the bot's login")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Activity (1 problems)" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Update the Bluesky login" }));
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(await screen.findByText(/Bluesky didn't accept this login/)).toBeInTheDocument();
  });

  it("shows the server's message when a change fails", async () => {
    const state = workingState();
    const { user } = renderApp("/accounts", state);
    await screen.findByText("Mira Draws", { selector: "b" });
    server.use(http.post("*/api/accounts/:id/poll_now/", () => HttpResponse.json({ code: "internal", message: "Something went wrong on the bot's side. Try again; if it keeps happening, check the activity log." }, { status: 500 })));
    await user.click(screen.getByRole("button", { name: "Check Mira Draws now" }));
    expect(await screen.findByText(/Something went wrong on the bot's side/)).toBeInTheDocument();
  });
});

describe("request security", () => {
  it("sends the CSRF token and JSON with every change", async () => {
    const { user, state } = renderApp("/accounts", workingState());
    await user.click(await screen.findByRole("button", { name: "Pause Mira Draws" }));
    await user.click(screen.getByRole("button", { name: "Check all now" }));
    await waitFor(() => expect(state.requests.length).toBe(2));
    expect(state.requests.every((r) => r.token === TOKEN)).toBe(true);
  });

  it("gets a fresh token and retries once when the token was refused", async () => {
    const state = workingState();
    state.token = "rotated-token"; // the cookie in the browser is now stale
    const { user } = renderApp("/accounts", state);
    await user.click(await screen.findByRole("button", { name: "Pause Mira Draws" }));
    await waitFor(() => expect(state.accounts[0].is_active).toBe(false));
    expect(changes(state)).toEqual(["PATCH /api/accounts/1/", "PATCH /api/accounts/1/"]);
    expect(state.requests.map((r) => r.token)).toEqual([TOKEN, "rotated-token"]);
  });

  it("never retries a request refused as cross-site", async () => {
    let calls = 0;
    server.use(
      http.post("*/api/accounts/check_all/", () => {
        calls += 1;
        return HttpResponse.json({ code: "cross_site_request", message: "This request came from another website, so it was refused." }, { status: 403 });
      }),
    );
    document.cookie = `csrftoken=${TOKEN}; path=/`;
    await expect(api.post("/api/accounts/check_all/")).rejects.toMatchObject({ code: "cross_site_request", status: 403 });
    expect(calls).toBe(1);
  });

  it("only ever talks to its own site", () => {
    expect(onThisSite("/api/status/")).toBe(`${window.location.origin}/api/status/`);
    expect(() => onThisSite("https://evil.example/api/")).toThrow();
    expect(() => onThisSite("//evil.example/api/")).toThrow();
  });

  it("turns field errors into messages", () => {
    const error = new ApiError(400, { handle: ["You're already watching @a.bsky.social."] });
    expect(error.message).toBe("You're already watching @a.bsky.social.");
    expect(error.fields).toEqual({ handle: ["You're already watching @a.bsky.social."] });
    expect(new ApiError(502, null).message).toMatch(/bot's side/);
  });
});

describe("activity", () => {
  it("filters to sent posts and problems", async () => {
    const { user } = renderApp("/activity", workingState());
    expect(await screen.findByText("“Commissions open for March!”")).toBeInTheDocument();
    expect(screen.getByText(/Checker started$/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Problems" }));
    expect(await screen.findByText(/Couldn't send to #announcements yet/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("“Commissions open for March!”")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Sent" }));
    expect(await screen.findByText("“Commissions open for March!”")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the post on Bluesky" })).toHaveAttribute("href", "https://bsky.app/profile/mira-draws.bsky.social/post/1");
  });

  it("loads older entries on request", async () => {
    const state = workingState();
    state.events = Array.from({ length: 60 }, (_, i) => ({ ...state.events[2], id: 1000 - i, message: `Entry ${i}` }));
    const { user } = renderApp("/activity", state);
    expect(await screen.findByText("Entry 49")).toBeInTheDocument();
    expect(screen.queryByText("Entry 50")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show older" }));
    expect(await screen.findByText("Entry 59")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show older" })).not.toBeInTheDocument();
  });

  it("never links post addresses outside Bluesky", async () => {
    const state = workingState();
    state.events[0].detail.post_url = "javascript:alert(1)";
    renderApp("/activity", state);
    await screen.findByText("“Commissions open for March!”");
    expect(screen.queryByRole("link", { name: "Open the post on Bluesky" })).not.toBeInTheDocument();
  });
});

describe("keyboard use", () => {
  it("has a skip link and named navigation", async () => {
    renderApp("/accounts", workingState());
    await screen.findByText("Mira Draws", { selector: "b" });
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getAllByRole("link").map((l) => l.getAttribute("aria-label"))).toEqual(["Accounts (3)", "Activity", "Settings"]);
  });

  it("moves through the actions menu with arrow keys and closes it with Escape", async () => {
    const { user } = renderApp("/accounts", workingState());
    const trigger = await screen.findByRole("button", { name: "More actions for Mira Draws" });
    trigger.focus();
    await user.keyboard("{ArrowDown}");
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    await waitFor(() => expect(screen.getByRole("menuitem", { name: "Edit" })).toHaveFocus());
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Open on Bluesky" })).toHaveFocus();
    await user.keyboard("{ArrowUp}{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: "Remove" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("closes the drawer with Escape", async () => {
    const { user } = renderApp("/accounts", workingState());
    await user.click(await screen.findByRole("button", { name: "Add account" }));
    const dialog = screen.getByRole("dialog", { name: "Add an account to watch" });
    expect(within(dialog).getByLabelText("Bluesky handle")).toHaveFocus();
    fireEvent(dialog, new Event("cancel", { cancelable: true })); // what the browser sends on Escape
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("opens the bot status from the sidebar", async () => {
    const { user } = renderApp("/accounts", workingState());
    await user.click(await screen.findByRole("button", { name: /Bot status: Running/ }));
    const dialog = screen.getByRole("dialog", { name: /Running normally/ });
    expect(within(dialog).getByText("Checker")).toBeInTheDocument();
    expect(within(dialog).getByText(/alertbot-demo.bsky.social: Working/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows a helpful page for an unknown address", async () => {
    renderApp("/nowhere", workingState());
    expect(await screen.findByText("This page doesn't exist")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Accounts" })).toHaveAttribute("href", "/accounts");
  });
});
