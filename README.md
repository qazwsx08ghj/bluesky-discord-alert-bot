# Bluesky → Discord Alert Bot

**English** | [繁體中文](README.zh-TW.md)

Get a Discord notification whenever **any public Bluesky account you choose** posts something new: artists, news accounts, creators, friends.

**No coding needed.** Install one program (Docker Desktop), double-click a file, and set everything up in a control panel in your web browser.

![The control panel](docs/screenshots/02-accounts.png)

---

## What it does

Every few minutes the bot looks at the Bluesky accounts you picked. When one of them posts something new, a message like this appears in your Discord channel:

```
┌──────────────────────────────────────────┐
│  New post from Someone Cool              │
│                                          │
│  "Hello everyone! Here's my new post."   │
│                                          │
│  @someone.bsky.social on Bluesky         │
└──────────────────────────────────────────┘
```

Clicking the title opens the post on Bluesky.

- **Any public account works.** It doesn't have to be yours.
- **Several Discord channels:** each account can notify its own channel, or your default one.
- **No flood of old posts.** When you add an account, its existing posts are skipped; only new ones are sent.
- **Runs on your own computer.** Your password and settings stay there; only the notifications go to Discord.

---

## What you need

- A Windows 10 or 11 computer (64-bit) that's on while you want notifications.
- **Docker Desktop.** The next section shows how to install it. Docker Desktop has its own license terms: if you use it for a business or another organisation, check that you qualify for free use ([Docker's license terms](https://docs.docker.com/subscription/desktop-license/)).
- Your own **Bluesky account**. The bot signs in with it to read posts.
- A **Discord server** where you can manage channels.

You don't need Python, Git or any other tools, and you never type commands.

---

## Step 1: Install Docker Desktop

Docker Desktop is the program the bot runs in. You install it once.

1. Go to **https://www.docker.com/products/docker-desktop/** and download **Docker Desktop for Windows**.
2. Run the installer. Keep **"Use WSL 2"** ticked.
3. **Restart the computer** when the installer asks.
4. Open **Docker Desktop** from the Start menu and accept the terms. You can skip signing in.
5. Wait until the bottom-left corner says **"Engine running"**.
6. In Docker Desktop's **Settings** (gear icon), tick **"Start Docker Desktop when you sign in to your computer"**, so the bot comes back by itself after a restart.

> **Docker Desktop asks to install or update "WSL":** let it, then restart the computer.
> **It says virtualization is turned off:** this is switched on in the computer's BIOS/UEFI settings. Search the web for your computer model plus "enable virtualization", or ask someone who knows your computer.

---

## Step 2: Download and install the bot

1. Download **`BlueskyDiscordAlertBot-stable.zip`** from the [latest release](https://github.com/qazwsx08ghj/bluesky-discord-alert-bot/releases/latest).
2. **Before unzipping:** right-click the zip, choose **Properties**, tick **Unblock** if you see it, and press **OK**. Otherwise Windows may warn you about each script.
3. Right-click the zip, choose **Extract All**, and put the folder somewhere permanent, for example **Documents**.
4. Double-click **`setup.bat`**.
   - If Windows says **"Windows protected your PC"**, choose **More info → Run anyway**. The scripts are plain text files you can open in Notepad.
   - The first time takes a few minutes.
   - When it's ready, the control panel opens in your browser at **http://127.0.0.1:8000**.

You can close the black window afterwards. The bot keeps running inside Docker Desktop.

---

## Step 3: Set it up in the control panel

The first time, the control panel walks you through four steps.

![Setup](docs/screenshots/01-setup.png)

### 1. Connect your Bluesky login (an App Password)

> **This is your own Bluesky login, not the accounts you want to follow.** Bluesky only lets signed-in apps read posts, even public ones. Think of it as a library card: the card is yours, but you can read any book.

Use an **App Password**, not your real password. It's safer, and you can delete it any time to cut the bot off.

1. Sign in at **bsky.app**.
2. Open **Settings → Privacy and Security → App Passwords**.
3. Choose **Add App Password**, name it `discord-bot`, and copy the password it shows (it looks like `xxxx-xxxx-xxxx-xxxx`). You won't see it again.
4. In the control panel, enter your Bluesky handle (for example `yourname.bsky.social`), paste the App Password, and press **Test and continue**.

### 2. Choose a Discord channel (a Webhook URL)

A webhook URL is a link from Discord that lets the bot post in one channel.

1. In Discord, hover over the channel for notifications and click its **gear icon** (Edit Channel).
2. Open **Integrations → Webhooks → New Webhook**.
3. Name it `Bluesky Bot` (the sender name shown in Discord) and choose **Copy Webhook URL**.
4. In the control panel, give the channel a name and paste the URL. **Keep the URL private:** anyone who has it can post in that channel.

### 3. Send a test message

Press **Send test message** and look in your Discord channel. If nothing arrives, the panel tells you what to check.

### 4. Add your first account

Type the handle of any public Bluesky account. The panel shows the profile so you can check it's the right one. **Nothing appears in Discord until that account posts something new.**

---

## Every day

Everything happens in the control panel at **http://127.0.0.1:8000** (bookmark it).

| Page | What you do there |
|---|---|
| **Accounts** | See whether the bot is running; add, edit, pause or remove accounts; **Check now** or **Check all now** |
| **Activity** | Everything the bot sent, and any problems with what to do about them |
| **Settings** | Your Bluesky login, your Discord channels, how often to check, light or dark look |

The status at the bottom of the sidebar shows whether the bot is running. If something needs your attention, a coloured banner explains it and offers the fix.

### The files in the bot's folder

| Double-click | What it does |
|---|---|
| `start.bat` | Starts the bot (if needed) and opens the control panel |
| `stop.bat` | Stops the bot until you run `start.bat`. Nothing is deleted. |
| `update.bat` | Installs the newest version. Makes a backup first; your accounts and settings are kept. |
| `backup.bat` | Saves a backup copy in the `backups` folder |
| `restore.bat` | Puts a backup back (it backs up the current data first) |
| `setup.bat` | First-time installation (safe to run again) |

**The bot runs while Docker Desktop runs.** If Docker Desktop is closed or the computer is off, notifications pause and start again by themselves when Docker Desktop is back.

---

## Backups and your data

- Your accounts, settings and history are kept **inside Docker Desktop** on this computer. Updating, restarting or running setup again never deletes them.
- A backup is made **automatically** before every update.
- **`backup.bat`** makes one whenever you like, in the **`backups`** folder next to the scripts.
- **Backups contain your App Password and webhook URLs.** Keep that folder private.
- **To go back to a backup,** double-click `restore.bat` and type the backup's number.

---

## Troubleshooting

The scripts explain problems in plain words, and they never delete your data.

**"Docker Desktop isn't installed" or "isn't running yet"**
→ Install or open Docker Desktop (Step 1) and wait for "Engine running". The scripts try to start it for you.

**"Docker Desktop is open, but it isn't responding"**
→ Answer **Y** and the script restarts Docker Desktop. If it keeps happening, restart the computer and install Docker Desktop's updates.

**"Another program is already using port 8000"**
→ If an older version of this bot is running in a black window, close it (two copies would send everything twice). Otherwise open `docker\compose.env` in Notepad, change `ALERTBOT_PORT=8000` to another number such as `8080`, and use `http://127.0.0.1:8080`.

**"Can't reach the bot" in the control panel**
→ Open Docker Desktop, then double-click `start.bat`. The page reconnects by itself.

**Nothing appears in Discord after adding an account**
→ That's normal: existing posts are skipped. The account's next new post will be sent.

**"Bluesky didn't accept the login"**
→ The App Password was deleted or mistyped. Make a new one (Step 3.1) and paste it under **Settings → Bluesky login**.

**The test message didn't arrive in Discord**
→ The webhook may have been deleted in Discord. Make a new one (Step 3.2) and paste its URL under **Settings → Discord channels → Edit**.

**Something else**
→ The scripts save a report in `logs\problem-report.txt` (passwords and webhook links are hidden). Share that file when you ask for help, and see [CHANGELOG.md](CHANGELOG.md) for what changed in each version.

---

## Coming from the earlier version (the black-window version)

1. Close the old bot's black window.
2. Install Docker Desktop (Step 1).
3. Put the new files in the **same folder**, double-click `setup.bat`, and answer **Y** when it offers to bring over your accounts and settings.

Your old files are only read, never changed. Don't start the old version again, or every post would be sent twice.

---

## For developers

Want to change the bot, run the tests or contribute? Start with [CONTRIBUTING.md](CONTRIBUTING.md). It explains the project's layout and how to work on the control panel or the backend on its own, and links to the technical documents:

- [docs/BACKEND.md](docs/BACKEND.md): the Django backend and the checker
- [docs/FRONTEND.md](docs/FRONTEND.md): the React control panel
- [docs/API.md](docs/API.md) and [docs/openapi.yaml](docs/openapi.yaml): the API
- [docs/DOCKER.md](docs/DOCKER.md): how the Docker version works, and how releases are made

To report a security problem, please follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## License

The bot is free and open source under the [MIT License](LICENSE). It includes other open-source software under its own licenses: see [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt), also linked in the control panel (Settings → About this bot).

Docker Desktop is a separate product by Docker, Inc., with [its own license](https://docs.docker.com/subscription/desktop-license/). This project isn't affiliated with Bluesky, Discord or Docker.
