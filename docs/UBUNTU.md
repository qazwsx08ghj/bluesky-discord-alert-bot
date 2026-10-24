# Running the bot on an Ubuntu server

[繁體中文](UBUNTU.zh-TW.md)

The same bot as the Windows download, for a Linux server: one command installs it, and one script (`./alertbot`) runs everything afterwards. Your accounts, settings and history live in Docker and are kept through restarts, updates and reinstalls.

## 1. Supported systems

- **Ubuntu Server 24.04 LTS** (tested) **or 22.04 LTS**, on **x86-64 (amd64)** or **ARM64** (for example a Raspberry Pi 4 or 5 with 64-bit Ubuntu). Other Linux systems with Docker and bash should work too, but are not tested.
- Linux support starts with version **1.0.0-rc.4**. Older versions have only the Windows download.

## 2. What you need

- **Docker Engine with its Compose plugin (2.23.0 or later).** Install them by following Docker's guide: https://docs.docker.com/engine/install/ubuntu/ . On Ubuntu 24.04, Ubuntu's own packages also work: `sudo apt install docker.io docker-compose-v2`. The script checks the version and says what is missing.
- **Permission to use Docker**: either run `sudo usermod -aG docker $USER`, then log out and back in (members of the `docker` group can control the whole server, like root), or put `sudo` in front of every `./alertbot` command. Use the same choice every time.
- `curl`, `tar` and `sha256sum` (standard on Ubuntu), and internet access for the first download.

## 3. Install

Run this in your home folder (copy all of it, paste it into the terminal, press Enter):

```sh
V=1.0.0-rc.4 && U=https://github.com/qazwsx08ghj/bluesky-discord-alert-bot/releases/download/v$V \
  && curl -fLO "$U/BlueskyDiscordAlertBot-$V-linux.tar.gz" -fLO "$U/SHA256SUMS" \
  && grep " BlueskyDiscordAlertBot-$V-linux.tar.gz\$" SHA256SUMS | sha256sum --check \
  && tar -xzf "BlueskyDiscordAlertBot-$V-linux.tar.gz" && cd bluesky-alert-bot && ./alertbot setup
```

What it does: it downloads the version `V` and its checksum list from that version's GitHub release (releases can't be changed after publishing), checks the download against the list, and only then unpacks it into `bluesky-alert-bot/` and starts the setup. If the check fails, nothing is unpacked or run. For a newer version, change `V` to the newest version on the [releases page](https://github.com/qazwsx08ghj/bluesky-discord-alert-bot/releases).

To also check who built it (optional, needs the [GitHub CLI](https://cli.github.com/)): `gh attestation verify BlueskyDiscordAlertBot-$V-linux.tar.gz --repo qazwsx08ghj/bluesky-discord-alert-bot`.

Running the command again is safe: it keeps your settings (`docker/compose.env`) and your data, and runs the setup again with your saved answers as the defaults. If the folder already has another version installed, that version is kept; install newer versions with `./alertbot update`.

## 4. Address and port

The setup asks two questions. Press Enter to keep the value in brackets.

```
 Bind IP [127.0.0.1]:
 Host port [8000]:
```

- **Bind IP**: where the control panel can be reached.
  - `127.0.0.1` (the default): only on the server itself. The safe choice; reach it from your computer with an SSH tunnel (section 6).
  - **a LAN or VPN address of the server** (for example `192.168.1.10`, or a Tailscale address `100.x.y.z`): other devices on that network. You must type `yes` after a warning.
  - `0.0.0.0`: every network the server is on. If the server has a public internet address, you must type `PUBLIC`. Don't, unless a firewall in front of the server blocks the port (section 10).
- **Host port**: the port on the server. If it is in use (by any program, IPv4 or IPv6), the setup says so and offers the next free one.

Your answers are saved in `docker/compose.env` (`ALERTBOT_BIND`, `ALERTBOT_PORT`, and the matching `ALERTBOT_ALLOWED_HOSTS` and `ALERTBOT_CSRF_TRUSTED_ORIGINS`), and kept by updates, restarts and restores.

## 5. First-time setup in the control panel

The first time, the control panel walks you through it, exactly as on Windows ([README](../README.md#step-3-set-it-up-in-the-control-panel)): connect your Bluesky login with an **App Password** (never your real password), add a Discord channel with a **webhook URL**, send a test message, and add the first account to watch.

## 6. Opening the control panel

`./alertbot status` shows the address. Then:

- **Bind IP 127.0.0.1**, from your own computer: open an SSH tunnel and keep it open while you use the panel:
  ```sh
  ssh -L 8000:127.0.0.1:8000 you@your-server
  ```
  then open http://127.0.0.1:8000 in your browser. (Use your port on both sides if it isn't 8000.)
- **A LAN or VPN address**: open `http://<that address>:<port>` from a device on that network.

## 7. Everyday commands

Run them in the `bluesky-alert-bot` folder:

| Command | What it does |
|---|---|
| `./alertbot status` | Is it running and healthy, which version, the panel's address |
| `./alertbot start` | Starts the bot. It also starts by itself after a reboot, as long as Docker does (`sudo systemctl enable docker`) and, with a LAN or VPN bind IP, that address exists when Docker starts (otherwise run `./alertbot start`). |
| `./alertbot stop` | Stops it until `./alertbot start` |
| `./alertbot update` | Installs a newer version: checks it first, backs up, and goes back by itself if it fails. If you're up to date, it changes nothing. |
| `./alertbot backup` | Saves a backup in the `backups` folder (it contains passwords: keep it private) |
| `./alertbot restore` | Puts a backup back. It says first what that would undo, and backs up the current data. |
| `./alertbot config` | Changes the address or port (section 8) |
| `./alertbot setup` | The first-time setup; safe to run again |

**Moving from Windows:** make a backup there (`backup.bat`), copy the `.sqlite3` file from its `backups` folder into `bluesky-alert-bot/backups/` on the server, then run `./alertbot restore` and choose it. Backups work on both systems.

## 8. Changing the address or port later

```sh
./alertbot config
```

It asks the same two questions, with your current settings as the defaults, saves them, and restarts the control panel if it is running. No reinstall, and nothing in the program changes.

## 9. Troubleshooting

- **"Docker isn't installed" / "isn't running"**: install Docker (section 2), or start it with `sudo systemctl enable --now docker`.
- **"this user may not use it" (permission denied)**: see section 2, "Permission to use Docker".
- **"Docker Compose … is too old"**: install a newer Compose plugin from Docker's repository (section 2).
- **"Port … is already in use"**: choose another port with `./alertbot config`. `sudo ss -ltnp` shows which program uses it.
- **The panel opens on the server but not from another device**: was the bind IP set to that network's address (`./alertbot status`)? Is the port allowed by the firewalls between them? With `127.0.0.1`, use the SSH tunnel.
- **"Bad Request (400)" in the browser**: the panel was opened under a name or address it wasn't set up for (for example a host name instead of the IP). Use the address `./alertbot status` shows, or run `./alertbot config` again.
- **"An update didn't finish"**: run `./alertbot update`. It finishes or undoes the interrupted update, safely.
- **"last used by a newer version"**: the data was updated by a newer version than this folder's. Use the newer version, or restore an older backup.
- **Anything else**: `./alertbot status`, and the report in `logs/problem-report.txt` (saved when starting fails; passwords and webhook links are hidden), help when you ask for help.

## 10. Remote access and security

The control panel has **no login**. Anyone who can reach it can read and change everything, including your Bluesky App Password and Discord webhooks. A static or public IP address doesn't change that.

- **Never make it reachable from the internet** (a public address, or `0.0.0.0` on a server with one). Docker publishes ports past `ufw`'s rules, so `ufw` alone doesn't block it; only a firewall outside the server (for example your cloud provider's) would.
- **Safest: keep `127.0.0.1` and use an SSH tunnel** (section 6). Nothing new is opened on the server.
- **From several devices: a VPN such as [Tailscale](https://tailscale.com/)**. Install it on the server and your devices, then choose the server's Tailscale address (`100.x.y.z`) as the bind IP. Only your own devices can reach it.
- **On a home or office LAN**: only if you trust every device on that network.
- **A reverse proxy with HTTPS and a login** in front of the panel (for example Caddy or nginx with basic authentication) is possible, but you set it up and maintain it yourself; keep the bind IP `127.0.0.1` and let only the proxy reach the panel.
