# 在 Ubuntu 伺服器上執行機器人

[English](UBUNTU.md)

和 Windows 版是同一個機器人，給 Linux 伺服器使用：一個指令完成安裝，之後用一個指令檔（`./alertbot`）處理所有事情。你的帳號、設定和紀錄存在 Docker 裡，重新開機、更新和重新安裝都會保留。

## 1. 支援的系統

- **Ubuntu Server 24.04 LTS**（已測試）**或 22.04 LTS**，**x86-64（amd64）** 或 **ARM64**（例如裝了 64 位元 Ubuntu 的 Raspberry Pi 4 或 5）。其他有 Docker 和 bash 的 Linux 系統應該也能用，但沒有經過測試。
- 從 **1.0.0-rc.4** 版開始支援 Linux。更早的版本只有 Windows 下載檔。

## 2. 需要準備

- **Docker Engine 和它的 Compose 外掛（2.23.0 或更新版本）**。請依照 Docker 的說明安裝：https://docs.docker.com/engine/install/ubuntu/ 。在 Ubuntu 24.04 上，也可以用 Ubuntu 自己的套件：`sudo apt install docker.io docker-compose-v2`。指令檔會檢查版本，並說明缺少什麼。
- **使用 Docker 的權限**：執行 `sudo usermod -aG docker $USER`，然後登出再登入（`docker` 群組的成員能像 root 一樣控制整台伺服器）；或是每個 `./alertbot` 指令前面都加上 `sudo`。之後請一直用同一種方式。
- `curl`、`tar` 和 `sha256sum`（Ubuntu 內建），以及第一次下載需要的網路連線。

## 3. 安裝

在你的家目錄執行（整段複製、貼到終端機、按 Enter）：

```sh
V=1.0.0-rc.4 && U=https://github.com/qazwsx08ghj/bluesky-discord-alert-bot/releases/download/v$V \
  && curl -fLO "$U/BlueskyDiscordAlertBot-$V-linux.tar.gz" -fLO "$U/SHA256SUMS" \
  && grep " BlueskyDiscordAlertBot-$V-linux.tar.gz\$" SHA256SUMS | sha256sum --check \
  && tar -xzf "BlueskyDiscordAlertBot-$V-linux.tar.gz" && cd bluesky-alert-bot && ./alertbot setup
```

它會從版本 `V` 的 GitHub 發行頁面（發行後無法再修改）下載檔案和檢查碼清單，用清單檢查下載的檔案，檢查通過後才解壓縮到 `bluesky-alert-bot/` 並開始設定。檢查沒通過的話，什麼都不會解壓縮或執行。要安裝較新的版本，把 `V` 改成[發行頁面](https://github.com/qazwsx08ghj/bluesky-discord-alert-bot/releases)上最新的版本。

想同時確認是誰建置的（選用，需要 [GitHub CLI](https://cli.github.com/)）：`gh attestation verify BlueskyDiscordAlertBot-$V-linux.tar.gz --repo qazwsx08ghj/bluesky-discord-alert-bot`。

重複執行這個指令是安全的：它會保留你的設定（`docker/compose.env`）和資料，並以你之前的回答為預設值再設定一次。如果資料夾裡已經安裝了其他版本，會保留那個版本；要安裝新版本請用 `./alertbot update`。

## 4. 位址和連接埠

設定時會問兩個問題。直接按 Enter 會使用括號裡的值。

```
 Bind IP [127.0.0.1]:
 Host port [8000]:
```

- **Bind IP**：控制面板可以從哪裡連線。
  - `127.0.0.1`（預設）：只有伺服器本身。最安全的選擇；請用 SSH 通道從你的電腦連線（第 6 節）。
  - **伺服器的區域網路或 VPN 位址**（例如 `192.168.1.10`，或 Tailscale 位址 `100.x.y.z`）：同一個網路上的其他裝置。看完警告後必須輸入 `yes`。
  - `0.0.0.0`：伺服器連接的所有網路。如果伺服器有公開的網際網路位址，必須輸入 `PUBLIC`。除非伺服器前面有防火牆擋住這個連接埠，否則不要這樣做（第 10 節）。
- **Host port**：伺服器上的連接埠。如果已經有程式在使用（IPv4 或 IPv6），會告訴你並提供下一個可用的連接埠。

你的回答存在 `docker/compose.env`（`ALERTBOT_BIND`、`ALERTBOT_PORT`，以及對應的 `ALERTBOT_ALLOWED_HOSTS` 和 `ALERTBOT_CSRF_TRUSTED_ORIGINS`），更新、重新啟動和還原都會保留。

## 5. 第一次在控制面板設定

第一次使用時，控制面板會一步一步引導你，和 Windows 版完全一樣（[README](../README.zh-TW.md)）：用 **App 密碼**（絕對不要用真正的密碼）連接 Bluesky 帳號、用 **Webhook 網址**加入 Discord 頻道、送出測試訊息，然後加入第一個要追蹤的帳號。

## 6. 打開控制面板

`./alertbot status` 會顯示網址。然後：

- **Bind IP 是 127.0.0.1**，從你自己的電腦：開一個 SSH 通道，使用面板時保持開著：
  ```sh
  ssh -L 8000:127.0.0.1:8000 you@your-server
  ```
  然後在瀏覽器打開 http://127.0.0.1:8000 。（如果連接埠不是 8000，兩邊都換成你的連接埠。）
- **區域網路或 VPN 位址**：從那個網路上的裝置打開 `http://<那個位址>:<連接埠>`。

## 7. 日常指令

在 `bluesky-alert-bot` 資料夾裡執行：

| 指令 | 用途 |
|---|---|
| `./alertbot status` | 是否在執行、是否正常、哪個版本、面板網址 |
| `./alertbot start` | 啟動機器人。只要 Docker 會自動啟動（`sudo systemctl enable docker`），重新開機後它也會自動啟動。 |
| `./alertbot stop` | 停止，直到執行 `./alertbot start` |
| `./alertbot update` | 安裝新版本：先檢查、備份，失敗會自動退回。已經是最新版時什麼都不會改。 |
| `./alertbot backup` | 備份到 `backups` 資料夾（備份含有密碼，請妥善保管） |
| `./alertbot restore` | 還原備份。會先說明還原會復原哪些內容，並先備份目前的資料。 |
| `./alertbot config` | 修改位址或連接埠（第 8 節） |
| `./alertbot setup` | 第一次設定；重複執行也安全 |

**從 Windows 搬過來：** 先在 Windows 上備份（`backup.bat`），把它 `backups` 資料夾裡的 `.sqlite3` 檔複製到伺服器的 `bluesky-alert-bot/backups/`，然後執行 `./alertbot restore` 並選擇它。備份在兩種系統上都能用。

## 8. 之後修改位址或連接埠

```sh
./alertbot config
```

它會問同樣的兩個問題，並以目前的設定為預設值，儲存後如果機器人正在執行，會重新啟動控制面板。不需要重新安裝，也不會改到程式本身。

## 9. 疑難排解

- **「Docker isn't installed」/「isn't running」**：安裝 Docker（第 2 節），或用 `sudo systemctl enable --now docker` 啟動它。
- **「this user may not use it」（permission denied）**：請看第 2 節「使用 Docker 的權限」。
- **「Docker Compose … is too old」**：從 Docker 的套件庫安裝較新的 Compose 外掛（第 2 節）。
- **「Port … is already in use」**：用 `./alertbot config` 選另一個連接埠。`sudo ss -ltnp` 會顯示是哪個程式在使用。
- **在伺服器上打得開面板，從其他裝置打不開**：Bind IP 是否設成那個網路的位址（`./alertbot status`）？兩者之間的防火牆是否允許這個連接埠？如果是 `127.0.0.1`，請用 SSH 通道。
- **瀏覽器顯示「Bad Request (400)」**：用了沒有設定過的名稱或位址打開面板（例如用主機名稱而不是 IP）。請用 `./alertbot status` 顯示的網址，或再執行一次 `./alertbot config`。
- **「An update didn't finish」**：執行 `./alertbot update`，它會安全地完成或復原被中斷的更新。
- **「last used by a newer version」**：資料被比這個資料夾更新的版本更新過。請使用較新的版本，或還原較舊的備份。
- **其他問題**：`./alertbot status` 的輸出，以及 `logs/problem-report.txt` 裡的報告（啟動失敗時會儲存；密碼和 Webhook 連結會隱藏），在你求助時很有幫助。

## 10. 遠端連線與安全

控制面板**沒有登入功能**。任何連得到它的人都能讀取和修改所有內容，包括你的 Bluesky App 密碼和 Discord Webhook。固定 IP 或公開 IP 都不會改變這一點。

- **絕對不要讓它能從網際網路連線**（公開位址，或在有公開位址的伺服器上用 `0.0.0.0`）。Docker 開放的連接埠會繞過 `ufw` 的規則，所以只靠 `ufw` 擋不住；只有伺服器外部的防火牆（例如雲端供應商的）才擋得住。
- **最安全：保持 `127.0.0.1`，使用 SSH 通道**（第 6 節）。伺服器上不需要開放任何新的東西。
- **要從多台裝置使用：用 VPN，例如 [Tailscale](https://tailscale.com/)**。在伺服器和你的裝置上安裝，然後把伺服器的 Tailscale 位址（`100.x.y.z`）設為 Bind IP。只有你自己的裝置連得到。
- **家裡或辦公室的區域網路**：只在你信任網路上每一台裝置時使用。
- **在面板前面加上有 HTTPS 和登入的反向代理**（例如 Caddy 或 nginx 加上基本驗證）是可行的，但需要你自己設定和維護；Bind IP 請保持 `127.0.0.1`，只讓代理伺服器連到面板。
