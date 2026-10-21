# Bluesky → Discord Alert Bot

This bot watches **any public Bluesky account** you choose — artists, news accounts, creators, anyone — and automatically posts their new updates as announcements in your Discord channel.

**No coding needed to use it** — just follow this guide from top to bottom.

> **New: Docker Desktop version.** Install Docker Desktop, then double-click `setup.bat`. See [docs/DOCKER.md](docs/DOCKER.md). The Python/venv steps below still work (the scripts moved to `dev\`); this guide is rewritten for Docker in a later step.

---

## What does it do?

You pick any public Bluesky accounts you want to follow (they don't have to be yours). Every few minutes the bot checks those accounts for new posts. When someone posts something new, a message like this appears in your Discord channel automatically:

```
┌──────────────────────────────────────────┐
│  New post from Someone Cool              │
│                                          │
│  "Hello everyone! Here's my new post."  │
│                                          │
│  @someone.bsky.social on Bluesky        │
└──────────────────────────────────────────┘
```

Clicking the title takes you directly to the post on Bluesky.

---

## Before you start — checklist

Make sure you have these ready before doing anything else:

- [ ] A Windows computer
- [ ] Python installed (see Step 1 if you don't have it)
- [ ] A Bluesky account of your own — **not** the accounts you want to watch, just one the bot uses as a "login key" to access Bluesky's system
- [ ] A Discord server where you have permission to add webhooks

---

## Step 1 — Install Python

> **Skip this step if Python is already installed.**
> To check: open the Start Menu, search for **Command Prompt**, open it, type `python --version` and press Enter. If you see a version number like `Python 3.11.x`, you already have it.

1. Go to **python.org/downloads**
2. Click the big yellow **Download Python** button
3. Run the installer
4. **Important:** On the first screen of the installer, check the box that says **"Add Python to PATH"** before clicking Install

---

## Step 2 — Open the project folder in Command Prompt

1. Find the folder named `DRF_backend_API_with_discord_alert_bot` on your computer
2. Hold **Shift** and **right-click** anywhere inside that folder
3. Click **"Open in Terminal"** or **"Open PowerShell window here"**

A black or blue window will open. Keep it open — you'll type commands here.

---

## Step 3 — Get your Bluesky App Password

> **This is YOUR Bluesky login — not the accounts you want to watch.**
> Bluesky requires the bot to be logged in before it can read anyone's posts, even public ones. Think of this like needing a library card to browse books — the card is yours, but you can read any book in the library.
> You can watch any public account (artists, news, creators) regardless of whose login you use here.

You should create a special **App Password** for the bot instead of using your real Bluesky password — it's safer and you can delete it any time.

1. Log in to **bsky.app**
2. Click your profile picture → **Settings**
3. Scroll down to **Privacy and Security**
4. Click **App Passwords** → **Add App Password**
5. Give it any name (e.g. `discord-bot`)
6. Click **Create App Password**
7. Copy the password shown (looks like `xxxx-xxxx-xxxx-xxxx`) — **you won't see it again**

---

## Step 4 — Get your Discord Webhook URL

A webhook URL is a special link Discord gives you so outside apps can post messages into a channel.

1. Open **Discord** on your computer
2. Go to the channel where you want bot announcements (e.g. `#announcements`)
3. Click the **gear icon** next to the channel name (hover over the channel to see it)
4. In the menu on the left, click **Integrations**
5. Click **Webhooks** → **New Webhook**
6. Give it a name like `Bluesky Bot` (this is what shows as the sender in Discord)
7. Click **Copy Webhook URL**

The URL looks like:
```
https://discord.com/api/webhooks/1234567890123456789/abcDEFghiJKLmno...
```

Keep this copied — you'll need it in the next step.

---

## Step 5 — Start the bot and open the setup page

Double-click **`start.bat`** in the project folder to start the bot.

Then open your browser and go to:
```
http://127.0.0.1:8000/
```

You will see the bot's control panel — a simple web page where you configure everything. **No file editing needed.**

Click the **⚙️ Settings** tab and fill in:

| Field | What to enter |
|-------|---------------|
| **Your Bluesky Handle** | **Your own** username (the login key), e.g. `john.bsky.social` |
| **App Password** | The App Password from Step 3 — not your real password |
| **Webhook URL** | The webhook URL you copied in Step 4 |

Click **Save** for each section. Then click **📨 Send Test Message** — if a message appears in your Discord channel, everything is connected correctly.

> **Your login has nothing to do with which accounts you watch.**
> The accounts you want to monitor are added in the **📋 Watched Accounts** tab — they can be any public Bluesky account.

4. Save the file (Ctrl + S) and close Notepad

---

## Step 6 — Run first-time setup (do this once only)

Double-click **`setup.bat`** in the project folder.

This will:
- Install everything the bot needs
- Create the bot's database

When it says **"Setup complete!"** you're ready.

---

## Step 7 — Start the bot and open the control panel

Double-click **`start.bat`** in the project folder.

A window will open and you'll see something like:

```
Starting Bluesky Discord Alert Bot...
Starting development server at http://127.0.0.1:8000/
Quit the server with CTRL-BREAK.
```

**The bot is now running.** Keep this window open — closing it stops the bot.

Now open your browser and go to:
```
http://127.0.0.1:8000/
```

This is the bot's **control panel** — everything is managed here.

---

## Step 8 — Configure the bot (Settings tab)

In the control panel, click the **⚙️ Settings** tab and fill in your details. See Step 5 above for what to enter in each field.

After saving, click **📨 Send Test Message** to confirm Discord is connected.

---

## Step 9 — Add Bluesky accounts to watch

This is where you pick **whose posts you want announced in Discord**. These can be any public Bluesky accounts — artists, journalists, news outlets, game developers, anyone.

1. In the control panel, click the **📋 Watched Accounts** tab
2. Enter the Bluesky handle you want to watch (e.g. `someartist.bsky.social`)
3. Optionally add a display name (shown in Discord)
4. Click **+ Add Account**
5. Click **🔄 Check Now** on the new account to sync it

> **Note:** The first "Check Now" silently reads recent posts without announcing them. This prevents your Discord from being flooded with old posts. Only brand-new posts from that point on will be announced.

---

## Daily use

| What you want to do | How |
|---------------------|-----|
| Start the bot | Double-click `start.bat` |
| Stop the bot | Press Ctrl + C in the bot window, then close it |
| Open the control panel | Go to `http://127.0.0.1:8000/` in your browser (bot must be running) |
| Add a new Bluesky account | Control panel → 📋 Watched Accounts → fill in the form → Add |
| Pause an account | Control panel → click **⏸ Pause** on that account |
| Resume an account | Control panel → click **▶ Resume** on that account |
| Remove an account | Control panel → click **🗑 Remove** on that account |
| Force an immediate check | Control panel → click **🔄 Check Now** on an account |
| Check all accounts right now | Control panel → click **🔄 Check All Now** |
| Change settings (login, webhook, interval) | Control panel → ⚙️ Settings tab |

---

## Sending different accounts to different Discord channels

By default, all accounts post to the channel you set in ⚙️ Settings.

If you want **one account** to post to a **different channel** instead:

1. Get a second webhook URL from that other Discord channel (repeat Step 4)
2. When adding the account, click **▸ Custom Discord channel for this account** to expand the optional field
3. Paste the second webhook URL there

That account's posts will now go to its own channel, while all others go to the default.

---

## Troubleshooting

**The bot window shows an error about `BLUESKY_HANDLE`**
→ The bot's Bluesky login isn't set up yet. Open the control panel's **Settings** tab and enter your handle and App Password. (On the very first start the bot copies them from `.env`, Step 5.)

**The bot window shows "Invalid identifier or password"**
→ Your Bluesky handle or App Password is wrong. Enter both again in the control panel's **Settings** tab. Make sure you used the App Password from Step 3, not your real Bluesky password.

**Nothing appears in Discord after adding an account**
→ This is normal for the very first check — old posts are ignored. Wait for the next poll (up to 5 minutes) after a new post is made on that Bluesky account.

**The Discord test message didn't appear**
→ Your webhook URL is wrong or expired. Go back to Step 4 and create a new webhook in Discord.

**`setup.bat` says "python is not recognized"**
→ Python wasn't added to PATH during installation. Uninstall Python and reinstall it, making sure to tick **"Add Python to PATH"** on the first screen.

**I want to reset everything**
→ Delete the file `db.sqlite3` in the project folder, then run `setup.bat` again. The settings are then copied from `.env` again, which always holds the last saved settings.

**I want to change how often the bot checks for new posts**
→ Change it in the control panel's **Settings** tab. It takes effect within a minute; no restart is needed.

**I changed `.env` but nothing happened**
→ After the first start, settings are saved in the bot's database and changed in the control panel. Edits to `.env` are ignored (the bot window says so). To load the `.env` values anyway, run `python manage.py import_env_settings --force` in the project's Command Prompt.

---

## Secret key info (for the person who set this up)

The `SECRET_KEY` in `.env` is already filled in. It's a security key for the bot — don't share it or post it online. If you ever need to generate a new one, run this in the project's Command Prompt:

```
venv\Scripts\activate
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Copy the output and replace the `SECRET_KEY` line in `.env`.
