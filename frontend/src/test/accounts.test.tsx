import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { workingState } from "./fakeApi";
import { changes, renderApp } from "./render";

const drawer = () => screen.getByRole("dialog", { name: /Add an account to watch|Edit / });
const row = (name: string) => screen.getByText(name, { selector: "b" }).closest("tr")!;

describe("watched accounts", () => {
  it("shows each account with its channel, status and last check", async () => {
    renderApp("/accounts", workingState());
    expect(await screen.findByText("Mira Draws", { selector: "b" })).toBeInTheDocument();
    expect(within(row("Mira Draws")).getByText("art-drops")).toBeInTheDocument();
    expect(within(row("Pixel Forge Games")).getByText("announcements")).toBeInTheDocument();
    expect(within(row("Pixel Forge Games")).getByText("default")).toBeInTheDocument();
    expect(within(row("Tamsin Okoro")).getByText("Paused")).toBeInTheDocument();
    expect(within(row("Mira Draws")).getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("3 accounts · 2 active · checked every 5 minutes")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Accounts (3)" })).toHaveAttribute("aria-current", "page");
  });

  it("summarises how the bot is doing", async () => {
    renderApp("/accounts", workingState());
    const summary = await screen.findByRole("region", { name: "Summary" });
    expect(await within(summary).findByText("Running")).toBeInTheDocument();
    expect(within(summary).getByText("Checks every 5 minutes")).toBeInTheDocument();
    expect(within(summary).getByText("None right now")).toBeInTheDocument();
  });

  it("pauses and resumes an account", async () => {
    const { user, state } = renderApp("/accounts", workingState());
    await user.click(await screen.findByRole("button", { name: "Pause Mira Draws" }));
    expect(await screen.findByText(/Mira Draws paused/)).toBeInTheDocument();
    expect(state.accounts[0].is_active).toBe(false);
    await user.click(await screen.findByRole("button", { name: "Resume Mira Draws" }));
    await waitFor(() => expect(state.accounts[0].is_active).toBe(true));
    expect(state.requests.map((r) => r.body)).toEqual([{ is_active: false }, { is_active: true }]);
  });

  it("checks one account now and says what happened", async () => {
    const { user, state } = renderApp("/accounts", workingState());
    await user.click(await screen.findByRole("button", { name: "Check Mira Draws now" }));
    expect(await screen.findByText("Checked Mira Draws. No new posts.")).toBeInTheDocument();
    expect(changes(state)).toEqual(["POST /api/accounts/1/poll_now/"]);
    expect(screen.getByRole("button", { name: "Check Tamsin Okoro now" })).toBeDisabled();
  });

  it("removes an account only after confirming", async () => {
    const { user, state } = renderApp("/accounts", workingState());
    await user.click(await screen.findByRole("button", { name: "More actions for Tamsin Okoro" }));
    await user.click(screen.getByRole("menuitem", { name: "Remove" }));
    const confirm = screen.getByRole("alertdialog", { name: "Stop watching Tamsin Okoro?" });
    expect(within(confirm).getByText(/Posts already sent to Discord stay in your channel/)).toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(state.requests).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "More actions for Tamsin Okoro" }));
    await user.click(screen.getByRole("menuitem", { name: "Remove" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove account" }));
    await waitFor(() => expect(changes(state)).toEqual(["DELETE /api/accounts/3/"]));
    expect(await screen.findByText("Stopped watching Tamsin Okoro.")).toBeInTheDocument();
    expect(changes(state)).toEqual(["DELETE /api/accounts/3/"]);
    await waitFor(() => expect(screen.queryByText("Tamsin Okoro", { selector: "b" })).not.toBeInTheDocument());
  });

  it("shows an empty state that leads to adding the first account", async () => {
    const state = workingState();
    state.accounts = [];
    const { user } = renderApp("/accounts", state);
    expect(await screen.findByText("No accounts yet")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add your first account" }));
    expect(drawer()).toBeInTheDocument();
  });
});

describe("adding and editing an account", () => {
  it("looks the profile up, fills in the name, adds it and runs the first check", async () => {
    const { user, state } = renderApp("/accounts", workingState());
    await user.click(await screen.findByRole("button", { name: "Add account" }));
    const add = within(drawer()).getByRole("button", { name: "Add account" });
    expect(add).toBeDisabled();
    await user.type(within(drawer()).getByLabelText("Bluesky handle"), "@studio-kite.bsky.social");
    expect(await within(drawer()).findByText("Studio Kite", { selector: "b" })).toBeInTheDocument();
    expect(within(drawer()).getByLabelText("Name shown in Discord")).toHaveValue("Studio Kite");
    await user.selectOptions(within(drawer()).getByLabelText("Post to channel"), "#art-drops");
    await user.click(within(drawer()).getByLabelText(/Replies/));
    await user.click(add);
    await waitFor(() => expect(state.accounts).toHaveLength(4));
    expect(state.requests[0].body).toEqual({
      handle: "studio-kite.bsky.social",
      is_active: true,
      display_name: "Studio Kite",
      channel_id: 2,
      announce_posts: true,
      announce_reposts: true,
      announce_replies: true,
    });
    await waitFor(() => expect(changes(state)).toContain("POST /api/accounts/4/poll_now/"));
    expect(await screen.findByText(/First check done for Studio Kite\. Noted 2 existing posts; none were sent/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("says when a handle doesn't exist and doesn't allow adding it", async () => {
    const { user } = renderApp("/accounts", workingState());
    await user.click(await screen.findByRole("button", { name: "Add account" }));
    await user.type(within(drawer()).getByLabelText("Bluesky handle"), "nobody.bsky.social");
    expect(await within(drawer()).findByText(/No Bluesky account is called @nobody.bsky.social/)).toBeInTheDocument();
    expect(within(drawer()).getByRole("button", { name: "Add account" })).toBeDisabled();
  });

  it("says when the account is already watched", async () => {
    const { user } = renderApp("/accounts", workingState());
    await user.click(await screen.findByRole("button", { name: "Add account" }));
    await user.type(within(drawer()).getByLabelText("Bluesky handle"), "mira-draws.bsky.social");
    expect(await within(drawer()).findByText("You're already watching @mira-draws.bsky.social.")).toBeInTheDocument();
    expect(within(drawer()).getByRole("button", { name: "Add account" })).toBeDisabled();
  });

  it("suggests a corrected handle", async () => {
    const { user } = renderApp("/accounts", workingState());
    await user.click(await screen.findByRole("button", { name: "Add account" }));
    await user.type(within(drawer()).getByLabelText("Bluesky handle"), "studio kite.bsky.social");
    await user.click(await within(drawer()).findByRole("button", { name: "Use studiokite.bsky.social" }));
    expect(within(drawer()).getByLabelText("Bluesky handle")).toHaveValue("studiokite.bsky.social");
  });

  it("can create a new channel for the account in the same step", async () => {
    const { user, state } = renderApp("/accounts", workingState());
    await user.click(await screen.findByRole("button", { name: "Add account" }));
    await user.type(within(drawer()).getByLabelText("Bluesky handle"), "greenhouse.bsky.social");
    await within(drawer()).findByText("Greenhouse Weekly", { selector: "b" });
    await user.selectOptions(within(drawer()).getByLabelText("Post to channel"), "Add a new channel…");
    await user.type(within(drawer()).getByLabelText("Channel name"), "garden");
    await user.type(within(drawer()).getByLabelText("Webhook URL"), "https://discord.com/api/webhooks/42/garden-token");
    await user.click(within(drawer()).getByRole("button", { name: "Add account" }));
    await waitFor(() => expect(state.accounts).toHaveLength(4));
    const garden = state.channels.find((c) => c.name === "garden")!;
    expect(state.accounts[3].channel_id).toBe(garden.id);
    expect(changes(state).slice(0, 2)).toEqual(["POST /api/webhooks/", "POST /api/accounts/"]);
  });

  it("edits the name and channel of an account", async () => {
    const { user, state } = renderApp("/accounts", workingState());
    await user.click(await screen.findByRole("button", { name: "More actions for Pixel Forge Games" }));
    await user.click(screen.getByRole("menuitem", { name: "Edit" }));
    const edit = screen.getByRole("dialog", { name: "Edit Pixel Forge Games" });
    expect(within(edit).getByLabelText("Bluesky handle")).toHaveAttribute("readonly");
    const name = within(edit).getByLabelText("Name shown in Discord");
    await user.clear(name);
    await user.type(name, "Pixel Forge");
    await user.selectOptions(within(edit).getByLabelText("Post to channel"), "#art-drops");
    await user.click(within(edit).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(state.accounts[1]).toMatchObject({ display_name: "Pixel Forge", channel_id: 2 }));
    expect(changes(state)).toEqual(["PATCH /api/accounts/2/"]);
    expect(await screen.findByText("Saved the changes to Pixel Forge.")).toBeInTheDocument();
  });
});
