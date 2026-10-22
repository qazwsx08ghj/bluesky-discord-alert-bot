# Bluesky → Discord Alert Bot

[English](README.md) | **繁體中文**

**你選的任何公開 Bluesky 帳號**（繪師、新聞帳號、創作者、朋友）只要一發新貼文，Discord 就會收到通知。

**完全不用寫程式。** 只要安裝一個程式（Docker Desktop）、點兩下一個檔案，其他設定都在瀏覽器的控制面板裡完成。

> 控制面板目前只有英文介面，下面會在英文按鈕旁邊附上中文說明。

![控制面板](docs/screenshots/02-accounts.png)

---

## 它可以做什麼

機器人每隔幾分鐘會去看一次你挑選的 Bluesky 帳號。只要其中有人發了新貼文，你的 Discord 頻道就會出現像這樣的訊息：

```
┌──────────────────────────────────────────┐
│  New post from Someone Cool              │
│                                          │
│  "Hello everyone! Here's my new post."   │
│                                          │
│  @someone.bsky.social on Bluesky         │
└──────────────────────────────────────────┘
```

點標題就會在 Bluesky 打開那篇貼文。

- **任何公開帳號都可以**，不一定要是你自己的。
- **可以用好幾個 Discord 頻道：** 每個帳號最多可以通知 10 個頻道，也可以只用你的預設頻道。在每個頻道還可以提及（@）一個 Discord 身分組，讓組員收到提醒。
- **不會一次洗出一堆舊貼文。** 新增帳號時會略過它原本的貼文，之後只通知新貼文。
- **在你自己的電腦上執行。** 密碼和設定都留在你的電腦裡，只有通知會送到 Discord。

---

## 開始前要準備

- 一台 Windows 10 或 11（64 位元）電腦，想收通知的時候要保持開機。
- **Docker Desktop。** 安裝方式請看下一節。Docker Desktop 有自己的授權條款，公司或組織使用的話，請先確認是否符合免費使用的資格（[Docker 授權條款](https://docs.docker.com/subscription/desktop-license/)）。
- 你自己的 **Bluesky 帳號**。機器人會用它登入來讀取貼文。
- 一個你能管理頻道的 **Discord 伺服器**。

不需要 Python、Git 或其他任何工具，也完全不用打指令。

---

## 第 1 步：安裝 Docker Desktop

Docker Desktop 是讓機器人執行的程式，只要裝一次。

1. 到 **https://www.docker.com/products/docker-desktop/** 下載 **Docker Desktop for Windows**。
2. 執行安裝程式，**"Use WSL 2"** 保持勾選。
3. 安裝程式要求時，**重新開機**。
4. 從「開始」功能表打開 **Docker Desktop**，同意使用條款。登入可以跳過。
5. 等左下角出現 **"Engine running"**（引擎執行中）。
6. 打開 Docker Desktop 的 **Settings**（齒輪圖示），勾選 **"Start Docker Desktop when you sign in to your computer"**（登入電腦時自動啟動）。這樣電腦重開後，機器人會自己恢復運作。

> **Docker Desktop 要求安裝或更新「WSL」：** 照著做就好，完成後重新開機。
> **出現虛擬化（virtualization）沒有開啟的訊息：** 要到電腦的 BIOS/UEFI 設定裡開啟。可以用「電腦型號 + 開啟虛擬化」上網搜尋，或請熟悉電腦的人幫忙。

---

## 第 2 步：下載並安裝機器人

1. 到[最新版本頁面](https://github.com/qazwsx08ghj/bluesky-discord-alert-bot/releases/latest)下載 **`BlueskyDiscordAlertBot-stable.zip`**。
2. **解壓縮之前：** 在 zip 檔上按右鍵 →「內容」，如果看到「解除封鎖」就勾選，再按「確定」。不然執行每個指令檔時，Windows 都可能跳出警告。
3. 在 zip 檔上按右鍵 →「全部解壓縮」，把資料夾放在不會移動的地方，例如「文件」。
4. 點兩下 **`setup.bat`**。
   - 如果 Windows 顯示 **「Windows 已保護您的電腦」**，請按「其他資訊」→「仍要執行」。這些指令檔都是純文字檔，可以用記事本打開來看。
   - 第一次會花幾分鐘。
   - 準備好之後，瀏覽器會自動打開控制面板：**http://127.0.0.1:8000**。

之後黑色視窗可以直接關掉，機器人會在 Docker Desktop 裡繼續運作。

---

## 第 3 步：在控制面板完成設定

第一次使用時，控制面板會帶你完成四個步驟。

![設定步驟](docs/screenshots/01-setup.png)

### 1. 連結你的 Bluesky 登入（應用程式密碼）

> **這裡要填的是你自己的 Bluesky 帳號，不是你想追蹤的帳號。** Bluesky 規定程式要登入才能讀取貼文，就算是公開貼文也一樣。可以想成借書證：證是你的，但什麼書都能借。

請使用**應用程式密碼（App Password）**，不要用你真正的密碼。這樣比較安全，而且隨時刪掉它就能停止機器人的存取。

1. 到 **bsky.app** 登入。
2. 打開「設定」→「隱私與安全」→「應用程式密碼」（Settings → Privacy and Security → App Passwords）。
3. 選「新增應用程式密碼」，名稱取 `discord-bot`，然後複製畫面上的密碼（格式像 `xxxx-xxxx-xxxx-xxxx`）。這組密碼之後不會再顯示。
4. 在控制面板輸入你的 Bluesky 帳號名稱（例如 `yourname.bsky.social`），貼上應用程式密碼，按 **Test and continue**（測試並繼續）。

### 2. 選擇 Discord 頻道（Webhook 網址）

Webhook 網址是 Discord 提供的連結，機器人可以透過它在某個頻道發訊息。

1. 在 Discord 把滑鼠移到要收通知的頻道上，點**齒輪圖示**（編輯頻道）。
2. 打開「整合」→「Webhook」→「新 Webhook」。
3. 名稱取 `Bluesky Bot`（這會是 Discord 上顯示的發送者名稱），然後選「複製 Webhook 網址」。
4. 在控制面板幫這個頻道取個名字，再貼上網址。**網址請不要外流：** 拿到網址的人都能在那個頻道發訊息。

### 3. 發送測試訊息

按 **Send test message**（發送測試訊息），然後到 Discord 頻道看看有沒有收到。沒收到的話，控制面板會告訴你要檢查哪裡。

### 4. 新增第一個帳號

輸入任一公開 Bluesky 帳號的名稱，控制面板會先顯示對方的個人檔案讓你確認。**在那個帳號發新貼文之前，Discord 不會收到任何通知。**

### 5. 選用：更多頻道，以及提及身分組

一個帳號可以把通知送到好幾個頻道：在 **Discord channels** 底下按 **Add another channel**。

每個頻道旁邊有一個可以不填的 **Role to mention** 欄位。留空就是一般通知。如果想提醒某個 Discord 身分組，就貼上該身分組的 ID：

1. 在 Discord 打開 **使用者設定 → 進階**，開啟 **開發者模式**。
2. 打開你伺服器的 **伺服器設定 → 身分組**。
3. 按身分組旁邊的 **…**，選 **複製身分組 ID**。

不能使用 @everyone 和 @here。修改只會影響之後的新貼文，舊貼文不會再送一次。

---

## 日常使用

所有操作都在控制面板 **http://127.0.0.1:8000** 進行（建議加入書籤）。

| 頁面 | 可以做什麼 |
|---|---|
| **Accounts**（帳號） | 看機器人有沒有在運作；新增、編輯、暫停或移除帳號；**Check now**（立即檢查）或 **Check all now**（全部立即檢查） |
| **Activity**（活動紀錄） | 機器人送出的所有通知，以及遇到的問題和處理方法 |
| **Settings**（設定） | Bluesky 登入、Discord 頻道、檢查頻率、淺色或深色外觀 |

側邊欄最下面會顯示機器人有沒有在運作。如果有事情需要你處理，畫面上會出現一條彩色提示，說明狀況並提供解決方法。

### 機器人資料夾裡的檔案

| 點兩下 | 用途 |
|---|---|
| `start.bat` | 啟動機器人（如果還沒啟動），並打開控制面板 |
| `stop.bat` | 停止機器人，直到你再執行 `start.bat`。不會刪除任何東西。 |
| `update.bat` | 更新到最新版本。會先備份，帳號和設定都會保留。 |
| `backup.bat` | 備份到 `backups` 資料夾 |
| `restore.bat` | 還原備份（會先把目前的資料備份起來） |
| `setup.bat` | 第一次安裝（重複執行也沒關係） |

**Docker Desktop 有在執行，機器人才會運作。** Docker Desktop 關掉或電腦關機時，通知會暫停；等 Docker Desktop 再次啟動，就會自動恢復。

### 換成較新的下載版本（測試版）

`update.bat` 只會更新到它那份下載對應的版本：一般（正式版）下載永遠會更新到最新版本。測試版（「rc」，例如 `1.0.0-rc.2`）則是各自獨立的下載。要換成較新的測試版：

1. 下載新的 zip，先**解除封鎖**（按右鍵 →**內容**），再用**全部解壓縮**放到一個**新的資料夾**。
2. 點兩下**新資料夾裡的 `update.bat`**。它會先備份你的資料，再安裝新版本。你的帳號、設定、頻道和紀錄都會保留。完成時，它會告訴你舊資料夾在哪裡。
3. 之後只用新的資料夾。確認新版本沒問題後，**刪除舊的資料夾**。之前的備份不會不見：新資料夾裡的 `restore.bat` 會把它們全部列出來。

**更新之後，不要再用舊資料夾裡的檔案。** 它們屬於舊版本，舊版本無法使用已經更新過的資料：
- 從 1.0.0-rc.2 開始，舊資料夾的指令檔會發現這件事：它會停下來顯示警告，什麼都不改，新版本的機器人也會繼續執行。
- **1.0.0-rc.1 的指令檔無法檢查這件事。** 它的 `start.bat`（或 `update.bat`）會把機器人換回舊版本：通知會停止，Accounts 頁面會出現錯誤。你的帳號和紀錄不會被改動。要修正，請點兩下新資料夾裡的 `start.bat`。

**如果更新失敗，** 畫面會說明發生什麼事，也不會刪掉任何東西。請再執行一次 `update.bat`，下載中斷是常見的原因。如果想回到原本的版本，請點兩下**舊資料夾裡的 `restore.bat`**，選名稱開頭是 `db-before-update` 的最新備份。它會先備份目前的資料，再放回那份備份，然後啟動舊版本。

---

## 備份與資料

- 帳號、設定和紀錄都存在這台電腦的 **Docker Desktop 裡**。更新、重開或重新執行安裝都不會刪掉它們。
- 每次更新前都會**自動備份**。
- 想手動備份時，點兩下 **`backup.bat`**，備份會存在指令檔旁邊的 **`backups`** 資料夾。
- **備份裡有你的應用程式密碼和 Webhook 網址**，請不要分享這個資料夾。
- **要還原備份**，點兩下 `restore.bat`，輸入備份的編號即可。

---

## 疑難排解

指令檔會用簡單的文字說明問題（訊息是英文），而且絕對不會刪除你的資料。

**"Docker Desktop isn't installed" 或 "isn't running yet"（沒有安裝或還沒啟動）**
→ 安裝或打開 Docker Desktop（第 1 步），等到出現 "Engine running"。指令檔也會試著幫你啟動。

**"Docker Desktop is open, but it isn't responding"（已打開但沒有回應）**
→ 回答 **Y**，指令檔會幫你重新啟動 Docker Desktop。如果一直發生，請重新開機，並安裝 Docker Desktop 的更新。

**"Your Docker Desktop is too old"（Docker Desktop 版本太舊）**
→ 更新 Docker Desktop（Docker Desktop → Check for updates），再重新執行指令檔。

**Docker Desktop 設定成「Windows containers」**
→ 在時鐘旁邊的鯨魚圖示上按右鍵，選 **Switch to Linux containers…**（切換到 Linux 容器）。

**"Another program is already using port 8000"（8000 連接埠被其他程式占用）**
→ 如果舊版機器人還開在黑色視窗裡，請把它關掉（兩個同時執行，每篇貼文都會通知兩次）。如果不是，用記事本打開 `docker\compose.env`，把 `ALERTBOT_PORT=8000` 改成別的數字，例如 `8080`，之後改用 `http://127.0.0.1:8080`。

**控制面板顯示 "Can't reach the bot"（連不到機器人）**
→ 打開 Docker Desktop，再點兩下 `start.bat`，頁面會自己重新連線。

**新增帳號後，Discord 沒有收到任何通知**
→ 這是正常的：原本的貼文會被略過，等那個帳號發下一篇新貼文就會通知。

**"Bluesky didn't accept the login"（Bluesky 不接受登入）**
→ 應用程式密碼被刪掉或打錯了。請重新建立一組（第 3 步的 1），貼到 **Settings → Bluesky login**（設定 → Bluesky 登入）。

**Discord 沒收到測試訊息**
→ 這個 Webhook 可能已經在 Discord 被刪除。請重新建立一個（第 3 步的 2），把網址貼到 **Settings → Discord channels → Edit**（設定 → Discord 頻道 → 編輯）。

**其他問題**
→ 指令檔會把報告存在 `logs\problem-report.txt`（密碼和 Webhook 連結都已隱藏），尋求協助時可以附上這個檔案。各版本的變更請看 [CHANGELOG.md](CHANGELOG.md)（英文）。

---

## 從舊版（黑色視窗版）升級

1. 關掉舊版機器人的黑色視窗。
2. 安裝 Docker Desktop（第 1 步）。
3. 把新版檔案放進**同一個資料夾**，點兩下 `setup.bat`，詢問要不要搬移帳號和設定時回答 **Y**。

舊的檔案只會被讀取，不會被修改。之後請不要再啟動舊版，不然每篇貼文都會通知兩次。

## 目前的限制

- **需要 Windows 和 Docker Desktop。** 一鍵指令檔只支援 Windows 10 和 11。
- **控制面板只有英文介面，而且只能在執行機器人的那台電腦上打開。** 手機或其他電腦都連不進去。這是刻意的設計，因為控制面板沒有登入機制。
- **機器人停很久的話，可能會漏掉一些貼文。** 機器人每幾分鐘檢查一次。如果停了很久（例如電腦關機，或 Docker Desktop 沒開），恢復後每個帳號只會補上最新的 100 篇，這段期間更早的貼文就會略過。
- **Windows 重新開機後的自動恢復還沒驗證過。** 照設計，重開機後機器人會自己恢復運作（見上面第 1 步的第 6 點），但這點還沒實際測試。重開機後建議打開控制面板確認一下；如果沒在運作，點兩下 `start.bat` 就好。
- **ARM 處理器的電腦：** 機器人也有支援 ARM，但只在模擬環境中測試過，還沒在真正的 ARM 電腦上驗證。

---

## 給開發者

想修改機器人、執行測試或參與開發，請先看 [CONTRIBUTING.zh-TW.md](CONTRIBUTING.zh-TW.md)。裡面說明了專案結構、如何分開開發控制面板或後端，也有技術文件的連結（以下文件只有英文版）：

- [docs/BACKEND.md](docs/BACKEND.md)：Django 後端與檢查程式
- [docs/FRONTEND.md](docs/FRONTEND.md)：React 控制面板
- [docs/API.md](docs/API.md) 和 [docs/openapi.yaml](docs/openapi.yaml)：API
- [docs/DOCKER.md](docs/DOCKER.md)：Docker 版本的運作方式，以及如何發行新版本
- [docs/TESTING.md](docs/TESTING.md)：所有測試的說明

發現安全性問題時，請依照 [SECURITY.md](SECURITY.md) 私下回報，不要開公開的 issue。

## 授權

這個機器人是採用 [MIT 授權](LICENSE)的免費開源軟體。它也包含其他開源軟體，這些軟體各有自己的授權：請看 [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)，控制面板裡也有連結（Settings → About this bot）。

Docker Desktop 是 Docker, Inc. 的另一項產品，有[自己的授權條款](https://docs.docker.com/subscription/desktop-license/)。本專案與 Bluesky、Discord 及 Docker 都沒有關係。

> 授權以英文的 [LICENSE](LICENSE) 原文為準，中文說明僅供參考。
