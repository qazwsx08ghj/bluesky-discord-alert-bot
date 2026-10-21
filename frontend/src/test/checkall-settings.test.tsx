import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SECRET_PASSWORD, SECRET_WEBHOOK, SECRET_WEBHOOK_2, workingState } from "./fakeApi";
import { changes, renderApp } from "./render";

describe("check all (background job)", () => {
  it("starts a job, shows its progress per account, and reports the result", async () => {
    const { user, state } = renderApp("/accounts", workingState());
    await user.click(await screen.findByRole("button", { name: "Check all now" }));
    const panel = await screen.findByRole("region", { name: /Check all/ });
    expect(changes(state)).toEqual(["POST /api/accounts/check_all/"]);
    expect(within(panel).getByText("Each account (3)")).toBeInTheDocument();
    expect(await within(panel).findByText("Check all finished", {}, { timeout: 8000 })).toBeInTheDocument();
    expect(within(panel).getByText("Checked 2 accounts. No new posts.")).toBeInTheDocument();
    expect(within(panel).getByText(/2 checked · 1 skipped · no new posts sent/)).toBeInTheDocument();
    expect(within(panel).getByText("Paused, so it wasn't checked.")).toBeInTheDocument();
    expect(within(panel).getAllByText("Checked")).toHaveLength(2);
    // The button is usable again once the job is done.
    expect(screen.getByRole("button", { name: "Check all now" })).toBeEnabled();
  });

  it("shows a running job again after the page is reloaded", async () => {
    const state = workingState();
    state.jobs = [];
    const first = renderApp("/accounts", state);
    await first.user.click(await screen.findByRole("button", { name: "Check all now" }));
    await screen.findByRole("region", { name: /Check all/ });
    first.unmount();
    // A "reload": a fresh app and cache, the same server.
    state.jobs[0].poll_after_seconds = 0.05;
    renderApp("/accounts", state);
    expect(await screen.findByRole("region", { name: /Check all/ })).toBeInTheDocument();
    expect(await screen.findByText("Check all finished", {}, { timeout: 8000 })).toBeInTheDocument();
  });

  it("returns the running job instead of starting a second one", async () => {
    const state = workingState();
    const { user } = renderApp("/accounts", state);
    await user.click(await screen.findByRole("button", { name: "Check all now" }));
    await waitFor(() => expect(state.jobs).toHaveLength(1));
    expect(state.jobs).toHaveLength(1);
  });

  it("can hide a finished result", async () => {
    const { user } = renderApp("/accounts", workingState());
    await user.click(await screen.findByRole("button", { name: "Check all now" }));
    await screen.findByText("Check all finished", {}, { timeout: 8000 });
    await user.click(screen.getByRole("button", { name: "Hide this result" }));
    expect(screen.queryByRole("region", { name: /Check all/ })).not.toBeInTheDocument();
  });
});

describe("settings", () => {
  it("never shows the saved App Password or full webhook URLs", async () => {
    const { container } = renderApp("/settings", workingState());
    expect(await screen.findByText("art-drops")).toBeInTheDocument();
    expect(await screen.findByText(/Connected as/)).toBeInTheDocument();
    const html = container.ownerDocument.documentElement.outerHTML;
    for (const secret of [SECRET_PASSWORD, SECRET_WEBHOOK, SECRET_WEBHOOK_2, "SECRET-token"]) {
      expect(html).not.toContain(secret);
    }
    expect(screen.getByLabelText("App Password")).toHaveValue("");
    expect(screen.getByText("https://discord.com/api/webhooks/1189000000000000001/****ents")).toBeInTheDocument();
  });

  it("saves a new App Password and clears the field afterwards", async () => {
    const { user, state } = renderApp("/settings", workingState());
    const password = await screen.findByLabelText("App Password");
    await user.type(password, "zzzz-yyyy-xxxx-wwww");
    await user.click(screen.getByRole("button", { name: "Save login" }));
    await waitFor(() => expect(state.password).toBe("zzzz-yyyy-xxxx-wwww"));
    expect(await screen.findByText("Bluesky login saved. The next check uses it.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("App Password")).toHaveValue(""));
  });

  it("keeps the saved App Password when only the handle changes", async () => {
    const { user, state } = renderApp("/settings", workingState());
    const handle = await screen.findByLabelText("Your Bluesky handle");
    await user.clear(handle);
    await user.type(handle, "newname.bsky.social");
    await user.click(screen.getByRole("button", { name: "Save login" }));
    await waitFor(() => expect(state.handle).toBe("newname.bsky.social"));
    expect(state.requests[0].body).toEqual({ bluesky_handle: "newname.bsky.social" });
    expect(state.password).toBe(SECRET_PASSWORD);
  });

  it("tests the saved login without saving anything", async () => {
    const { user, state } = renderApp("/settings", workingState());
    await user.click(await screen.findByRole("button", { name: "Test login" }));
    expect(await screen.findByText(/The login works/)).toBeInTheDocument();
    expect(changes(state)).toEqual(["POST /api/settings/test-login/"]);
    expect(state.requests[0].body).toEqual({ bluesky_handle: "alertbot-demo.bsky.social", bluesky_password: "" });
  });

  it("changes how often the bot checks", async () => {
    const { user, state } = renderApp("/settings", workingState());
    const select = await screen.findByLabelText("Check every");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await user.selectOptions(select, "10 minutes");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(state.interval).toBe(10));
    expect(await screen.findByText("Saved. The bot now checks every 10 minutes.")).toBeInTheDocument();
  });

  it("adds a channel, sends it a test message and makes it the default", async () => {
    const { user, state } = renderApp("/settings", workingState());
    await user.click(await screen.findByRole("button", { name: "Add channel" }));
    const dialog = screen.getByRole("dialog", { name: "Add a Discord channel" });
    await user.type(within(dialog).getByLabelText("Channel name"), "news");
    await user.type(within(dialog).getByLabelText("Webhook URL"), "https://discord.com/api/webhooks/77/news-token");
    await user.click(within(dialog).getByRole("button", { name: "Add channel" }));
    expect(await screen.findByText("Added #news. Send a test message to check it works.")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Send a test message to #news" }));
    expect(await screen.findByText("Test message sent. Check #news in Discord.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "More actions for #news" }));
    await user.click(screen.getByRole("menuitem", { name: "Make default" }));
    await waitFor(() => expect(state.channels.find((c) => c.is_default)?.name).toBe("news"));
  });

  it("asks where accounts should go before deleting a channel they use", async () => {
    const { user, state } = renderApp("/settings", workingState());
    await user.click(await screen.findByRole("button", { name: "More actions for #art-drops" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));
    const confirm = screen.getByRole("alertdialog", { name: "Delete #art-drops?" });
    expect(within(confirm).getByText(/1 account posts here/)).toBeInTheDocument();
    expect(within(confirm).getByLabelText("Move it to")).toHaveValue("default");
    await user.click(within(confirm).getByRole("button", { name: "Delete channel" }));
    await waitFor(() => expect(state.channels.map((c) => c.name)).toEqual(["announcements"]));
    expect(changes(state)).toEqual(["DELETE /api/webhooks/2/?reassign_to=default"]);
    expect(state.accounts[0].channel_id).toBeNull();
  });

  it("explains that the default channel can't be deleted", async () => {
    const { user, state } = renderApp("/settings", workingState());
    await user.click(await screen.findByRole("button", { name: "More actions for #announcements" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));
    const dialog = screen.getByRole("alertdialog", { name: "#announcements is the default channel" });
    expect(within(dialog).queryByRole("button", { name: "Delete channel" })).not.toBeInTheDocument();
    expect(state.requests).toHaveLength(0);
  });

  it("switches between light, dark and the computer's setting", async () => {
    const { user } = renderApp("/settings", workingState());
    await user.click(await screen.findByRole("button", { name: "Dark" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(window.localStorage.getItem("alertbot-theme")).toBe("dark");
    await user.click(screen.getByRole("button", { name: "Same as this computer" }));
    expect(document.documentElement).not.toHaveAttribute("data-theme");
    expect(screen.getByRole("button", { name: "Same as this computer" })).toHaveAttribute("aria-pressed", "true");
  });
});
