import { screen, waitFor, within } from "@testing-library/react";
import { http } from "msw";
import { describe, expect, it } from "vitest";
import { emptyState } from "./fakeApi";
import { changes, renderApp } from "./render";

const WEBHOOK = "https://discord.com/api/webhooks/123456789/abcDEF_token";

function step(n: number) {
  return screen.getByRole("heading", { name: new RegExp(`^Step ${n}:`) }).closest("li")!;
}

describe("first-time setup", () => {
  it("opens the setup steps on the first visit", async () => {
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Set up your bot" })).toBeInTheDocument();
    expect(screen.getByText(/^0 of 4 done\. About five minutes/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Setup" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: /Bot status: Not set up yet/ })).toBeInTheDocument();
  });

  it("never loses what was typed when the saved settings arrive late", async () => {
    // GitHub's CI machines are slower than a desktop: the settings can arrive after typing started.
    let release!: () => void;
    const settingsLoaded = new Promise<void>((resolve) => (release = resolve));
    const slowSettings = http.get("*/api/settings/", async () => {
      await settingsLoaded; // then falls through to the fake API's answer
    });
    const { user, state } = renderApp("/setup", emptyState(), { before: [slowSettings] });
    expect(await screen.findByText("Loading the settings…")).toBeInTheDocument();
    expect(screen.queryByLabelText("Your Bluesky handle")).not.toBeInTheDocument();
    release();
    await user.type(await screen.findByLabelText("Your Bluesky handle"), "me.bsky.social");
    await user.type(screen.getByLabelText("App Password"), "abcd-efgh-ijkl-mnop");
    expect(screen.getByLabelText("Your Bluesky handle")).toHaveValue("me.bsky.social");
    await user.click(screen.getByRole("button", { name: "Test and continue" }));
    await waitFor(() => expect(state.password).toBe("abcd-efgh-ijkl-mnop"));
  });

  it("goes through all four steps, testing the login before saving it", async () => {
    const { user, state } = renderApp("/setup");

    // 1. Bluesky login: tested first, saved only when it works.
    await user.type(await screen.findByLabelText("Your Bluesky handle"), "me.bsky.social");
    await user.type(screen.getByLabelText("App Password"), "abcd-efgh-ijkl-mnop");
    await user.click(screen.getByRole("button", { name: "Test and continue" }));
    await waitFor(() => expect(state.handle).toBe("me.bsky.social"));
    expect(state.password).toBe("abcd-efgh-ijkl-mnop");
    expect(changes(state)).toEqual(["POST /api/settings/test-login/", "PATCH /api/settings/"]);

    // 2. Channel: becomes the default.
    await waitFor(() => expect(within(step(2)).getByLabelText("Channel name")).toBeInTheDocument());
    await user.type(within(step(2)).getByLabelText("Channel name"), "announcements");
    await user.type(within(step(2)).getByLabelText("Webhook URL"), WEBHOOK);
    await user.click(within(step(2)).getByRole("button", { name: "Save and continue" }));
    await waitFor(() => expect(state.channels).toHaveLength(1));
    expect(state.channels[0]).toMatchObject({ name: "announcements", is_default: true });

    // 3. Test message.
    await user.click(await screen.findByRole("button", { name: "Send test message" }));
    expect(await screen.findByText("Test message sent. Check #announcements in Discord.")).toBeInTheDocument();
    expect(state.discord).toHaveLength(1);

    // 4. First account, from the drawer; its first check runs by itself.
    await user.click(await within(step(4)).findByRole("button", { name: "Add account" }));
    await user.type(await screen.findByLabelText("Bluesky handle"), "studio-kite.bsky.social");
    expect(await screen.findByText("Studio Kite", { selector: "b" })).toBeInTheDocument();
    await user.click(within(screen.getByRole("dialog", { name: "Add an account to watch" })).getByRole("button", { name: "Add account" }));
    await waitFor(() => expect(state.accounts).toHaveLength(1));
    await waitFor(() => expect(changes(state)).toContain(`POST /api/accounts/${state.accounts[0].id}/poll_now/`));
    expect(await screen.findByText(/First check done for Studio Kite/)).toBeInTheDocument();
    expect(await screen.findByText("Your bot is set up")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Setup" })).not.toBeInTheDocument();
  });

  it("doesn't save a login that Bluesky rejects", async () => {
    const { user, state } = renderApp("/setup");
    await user.type(await screen.findByLabelText("Your Bluesky handle"), "me.bsky.social");
    await user.type(screen.getByLabelText("App Password"), "wrong-pass-word-0000");
    await user.click(screen.getByRole("button", { name: "Test and continue" }));
    expect(await screen.findByText(/Bluesky didn't accept this handle and App Password/)).toBeInTheDocument();
    expect(state.password).toBe("");
    expect(changes(state)).toEqual(["POST /api/settings/test-login/"]);
  });

  it("offers to save without testing when Bluesky can't be reached", async () => {
    const { user, state } = renderApp("/setup");
    await user.type(await screen.findByLabelText("Your Bluesky handle"), "me.bsky.social");
    await user.type(screen.getByLabelText("App Password"), "offline-test-pass");
    await user.click(screen.getByRole("button", { name: "Test and continue" }));
    expect(await screen.findByText(/Couldn't reach Bluesky/)).toBeInTheDocument();
    expect(state.password).toBe("");
    await user.click(screen.getByRole("button", { name: "Save without testing" }));
    await waitFor(() => expect(state.password).toBe("offline-test-pass"));
  });

  it("explains missing fields before asking the server", async () => {
    const { user, state } = renderApp("/setup");
    await user.click(await screen.findByRole("button", { name: "Test and continue" }));
    expect(screen.getByText(/Enter the handle of your own Bluesky account/)).toBeInTheDocument();
    expect(screen.getByText(/Paste an App Password from Bluesky/)).toBeInTheDocument();
    expect(screen.getByLabelText("Your Bluesky handle")).toHaveAttribute("aria-invalid", "true");
    expect(state.requests).toHaveLength(0);
  });

  it("shows the server's message for a webhook URL that isn't Discord's, without repeating the URL", async () => {
    const state = emptyState();
    state.handle = "me.bsky.social";
    state.password = "abcd-efgh-ijkl-mnop";
    const { user } = renderApp("/setup", state);
    await user.type(await within(await waitFor(() => step(2))).findByLabelText("Channel name"), "news");
    await user.type(within(step(2)).getByLabelText("Webhook URL"), "https://example.com/hook");
    await user.click(within(step(2)).getByRole("button", { name: "Save and continue" }));
    expect(await screen.findByText(/It starts with https:\/\/discord.com\/api\/webhooks\//)).toBeInTheDocument();
    expect(state.channels).toHaveLength(0);
  });

  it("lets the test message be skipped and reports a failed test with a way to fix it", async () => {
    const state = emptyState();
    state.handle = "me.bsky.social";
    state.password = "abcd-efgh-ijkl-mnop";
    state.channels = [{ id: 1, name: "news", url: "https://discord.com/api/webhooks/1/broken", is_default: true, last_delivery: null }];
    const { user } = renderApp("/setup", state);
    await user.click(await screen.findByRole("button", { name: "Send test message" }));
    expect(await screen.findByText(/Discord didn't accept the test message/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Change the webhook URL" }));
    expect(within(step(2)).getByLabelText("Webhook URL")).toBeInTheDocument();
  });

  it("skipping the test message moves on to the first account", async () => {
    const state = emptyState();
    state.handle = "me.bsky.social";
    state.password = "abcd-efgh-ijkl-mnop";
    state.channels = [{ id: 1, name: "news", url: "https://discord.com/api/webhooks/1/abc", is_default: true, last_delivery: null }];
    const { user } = renderApp("/setup", state);
    await user.click(await screen.findByRole("button", { name: "Skip for now" }));
    expect(await within(step(4)).findByRole("button", { name: "Add account" })).toBeInTheDocument();
    expect(screen.getByText(/^3 of 4 done\./)).toBeInTheDocument();
  });
});
