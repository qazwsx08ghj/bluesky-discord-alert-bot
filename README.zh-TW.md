# Bluesky → Discord Alert Bot

[English](README.md) | **繁體中文**

這個機器人會追蹤**您選擇的任何公開 Bluesky 帳號**（繪師、新聞帳號、創作者，任何人都可以），並把他們的新貼文以公告的形式發到您的 Discord 頻道。

**不需要寫程式。** 您只要安裝一個程式 Docker Desktop，再按兩下一個檔案。其他所有事情都在網頁瀏覽器裡的控制面板完成。

> 控制面板目前只有英文介面。本說明會在英文按鈕名稱旁附上中文說明。

![控制面板](docs/screenshots/02-accounts.png)

---

## 功能

機器人每隔幾分鐘會查看您挑選的 Bluesky 帳號。當其中一個帳號發了新貼文，您的 Discord 頻道就會出現像這樣的訊息：

```
┌──────────────────────────────────────────┐
│  New post from Someone Cool              │
│                                          │
│  "Hello everyone! Here's my new post."   │
│                                          │
│  @someone.bsky.social on Bluesky         │
└──────────────────────────────────────────┘
```

點一下標題就會在 Bluesky 開啟那篇貼文。

- **任何公開帳號都可以。** 不一定要是您自己的帳號。
- **可以使用多個 Discord 頻道：** 每個追蹤的帳號可以發到自己的頻道，或發到您的預設頻道。
- **不會公告舊貼文。** 新增帳號時，機器人只會記下它現有的貼文而不發送，所以您的頻道不會被洗版。
- **在您自己的電腦上執行。** 您的密碼和設定都不會離開這台電腦，只有公告會送到 Discord。

---

## 需要準備的東西

- 一台 Windows 10 或 Windows 11（64 位元）電腦，在您想收到公告的期間保持開機。
- **Docker Desktop**。下一節會說明如何安裝。Docker Desktop 是 Docker, Inc. 的產品，有它自己的授權條款：個人使用、教育用途、非商業開源專案，以及小型企業（員工少於 250 人**而且**年營收少於 1,000 萬美元）可以免費使用。規模較大的組織和政府機關需要購買 Docker 的付費訂閱。詳見 [Docker 的授權條款](https://docs.docker.com/subscription/desktop-license/)。這個機器人本身則是所有人都可以免費使用（見[授權](#授權)）。
- 一個您自己的 **Bluesky 帳號**。機器人會用它登入來讀取貼文。
- 一個您可以管理頻道的 **Discord 伺服器**。

您不需要 Python、Node.js、Git 或任何其他工具，也完全不需要輸入指令。

---

## 第 1 步：安裝 Docker Desktop

Docker Desktop 是機器人在裡面執行的程式。只需要安裝一次。

1. 前往 **https://www.docker.com/products/docker-desktop/**，下載 **Docker Desktop for Windows**。
2. 開啟下載的檔案，依照安裝程式的步驟進行。請保持勾選 **"Use WSL 2"**（使用 WSL 2）。
3. 安裝程式要求時，**重新開機**。
4. 從「開始」功能表開啟 **Docker Desktop**。詢問時請接受條款。可以跳過登入，不需要登入。
5. 等到左下角顯示 **"Engine running"**（引擎執行中）。
6. 開啟 Docker Desktop 的 **Settings**（齒輪圖示），確認已勾選 **"Start Docker Desktop when you sign in to your computer"**（登入電腦時啟動 Docker Desktop）。這樣電腦重新開機後，機器人會自動恢復執行。

> **如果 Docker Desktop 說需要安裝或更新 "WSL"，** 請讓它自動處理，或依照它顯示的連結操作，然後重新開機。
> **如果它說虛擬化（virtualization）已關閉，** 需要到電腦的 BIOS/UEFI 設定裡開啟。可以用您的電腦型號加上「開啟虛擬化」上網搜尋，或請熟悉您電腦的人幫忙。

---

## 第 2 步：安裝機器人

1. 把機器人的資料夾放在固定的位置，例如**文件**資料夾。（如果您下載的是 `.zip` 檔，請先在檔案上按右鍵，選擇**全部解壓縮**。）
   - **解壓縮下載的 `.zip` 之前：** 在檔案上按右鍵，選擇**內容**，如果有**解除封鎖**的選項就勾選它，再按**確定**。Windows 會標記從網路下載的檔案，不這樣做的話，執行每個指令檔時都可能跳出警告。
   - 如果 Windows 仍然顯示 **「Windows 已保護您的電腦」**，請選擇**其他資訊 → 仍要執行**。這些指令檔都是純文字檔，您可以用記事本開啟，看看它們做了什麼。
2. 按兩下 **`setup.bat`**。
   - 第一次會花幾分鐘：它會在 Docker Desktop 裡準備好機器人。
   - 如果您在同一個資料夾用過這個機器人的**舊版本**，安裝程式會詢問是否要把帳號和設定搬過來。請回答 **Y**。舊檔案只會被讀取，不會被修改。
   - 準備好之後，控制面板會在瀏覽器中開啟，網址是 **http://127.0.0.1:8000**。

之後可以關閉黑色視窗。機器人會在 Docker Desktop 裡繼續執行。

---

## 第 3 步：在控制面板中設定

第一次使用時，控制面板會帶您完成四個步驟。

![設定](docs/screenshots/01-setup.png)

### 1. 連結您的 Bluesky 登入資訊（應用程式密碼）

> **這是您自己的 Bluesky 登入資訊，不是您想追蹤的帳號。** Bluesky 只允許已登入的應用程式讀取貼文，即使是公開貼文也一樣。可以把它想成借書證：借書證是您的，但您可以讀任何一本書。

請使用**應用程式密碼（App Password）**，不要用您真正的密碼。這樣比較安全，而且您隨時可以刪除它來切斷機器人的存取。

1. 在 **bsky.app** 登入。
2. 開啟**設定 → 隱私與安全 → 應用程式密碼**。
3. 選擇**新增應用程式密碼**，命名為 `discord-bot`，然後複製它顯示的密碼（格式類似 `xxxx-xxxx-xxxx-xxxx`）。之後就看不到這組密碼了。
4. 在控制面板輸入您的 Bluesky 帳號名稱（例如 `yourname.bsky.social`），貼上應用程式密碼，然後按 **Test and continue**（測試並繼續）。機器人會先確認能登入才儲存。

### 2. 選擇 Discord 頻道（Webhook 網址）

Webhook 網址是 Discord 提供的特殊連結，讓機器人可以在某一個頻道發文。

1. 在 Discord 中，把滑鼠移到要發公告的頻道上，點它的**齒輪圖示**（編輯頻道）。
2. 開啟**整合 → Webhook → 新 Webhook**。
3. 命名為 `Bluesky Bot`（這是在 Discord 顯示的發送者名稱），然後選擇**複製 Webhook 網址**。
4. 在控制面板為這個頻道取個名字，並貼上網址。請保密這個網址：任何拿到它的人都能在那個頻道發文。

### 3. 發送測試訊息

按 **Send test message**（發送測試訊息），然後查看您的 Discord 頻道。如果沒有收到，控制面板會告訴您該檢查什麼。

### 4. 新增第一個帳號

輸入任何公開 Bluesky 帳號的名稱。控制面板會先查詢並顯示個人檔案，讓您確認後再新增。第一次檢查會立刻執行，但只會記下現有的貼文，所以**在那個帳號發新貼文之前，Discord 不會出現任何訊息。**

---

## 日常使用

所有操作都在控制面板 **http://127.0.0.1:8000** 進行（建議加入書籤）。

| 頁面 | 可以做的事 |
|---|---|
| **Accounts**（帳號） | 查看機器人是否在執行、新增帳號、**Check now**（立即檢查）、**Pause / Resume**（暫停／繼續）、編輯或移除帳號、**Check all now**（全部立即檢查） |
| **Activity**（活動） | 機器人發送的所有內容，以及遇到的問題和處理方法 |
| **Settings**（設定） | 您的 Bluesky 登入資訊、Discord 頻道（新增、測試、變更預設頻道）、檢查頻率、淺色或深色外觀 |

側邊欄底部的狀態會顯示機器人是否在執行，點一下可以看詳細資訊。如果有需要您處理的事情，會出現一條彩色橫幅說明狀況並提供解決方法。

### 機器人資料夾裡的檔案

| 按兩下 | 用途 |
|---|---|
| `start.bat` | 啟動機器人（如果還沒在執行），並開啟控制面板 |
| `stop.bat` | 停止機器人，不會刪除任何東西。在執行 `start.bat` 之前會保持停止。 |
| `update.bat` | 安裝新版本。會先備份，您的帳號和設定都會保留。 |
| `backup.bat` | 把備份存到 `backups` 資料夾 |
| `restore.bat` | 還原備份。它會列出備份（最新的在最前面），並在還原前先備份目前的資料。 |
| `setup.bat` | 第一次安裝（重複執行也是安全的） |

**Docker Desktop 執行時，機器人才會執行。** 關閉 Docker Desktop 或關機會暫停公告；Docker Desktop 恢復後會自動重新開始。期間發的貼文會在下一次檢查時公告。

---

## 備份與您的資料

- 您的帳號、設定和紀錄都儲存在這台電腦的 **Docker Desktop 裡**。更新、重新啟動或重新執行安裝都不會刪除它們。
- 每次更新和每次資料庫變更之前，都會**自動備份**。
- **`backup.bat`** 可以隨時手動備份。備份會存在指令檔旁邊的 **`backups`** 資料夾。
- **備份裡含有您的應用程式密碼和 Webhook 網址。** 請保密這個資料夾，不要分享給別人。
- **要還原備份，** 請按兩下 `restore.bat`，然後輸入備份的編號。

---

## 疑難排解

每個指令檔都會用淺白的文字說明問題，而且絕不會刪除您的資料。（指令檔的訊息是英文。）

**"Docker Desktop isn't installed" 或 "isn't running yet"（尚未安裝或尚未執行）**
→ 安裝或開啟 Docker Desktop（第 1 步），等到顯示 "Engine running"。指令檔會嘗試幫您啟動它。

**"Docker Desktop is open, but it isn't responding"（已開啟但沒有回應）**
→ 回答 **Y**，指令檔會幫您重新啟動 Docker Desktop。如果一直發生，請重新開機，並安裝 Docker Desktop 的更新。

**"Another program is already using port 8000"（8000 連接埠已被其他程式使用）**
→ 如果有舊版機器人在黑色視窗中執行，請關閉它（同時執行兩份會讓每篇貼文都公告兩次）。否則，用記事本開啟 `docker\compose.env`，把 `ALERTBOT_PORT=8000` 改成其他數字，例如 `8080`，然後改用 `http://127.0.0.1:8080`。

**控制面板顯示 "Can't reach the bot"（無法連線到機器人）**
→ 開啟 Docker Desktop，然後按兩下 `start.bat`。頁面會自動重新連線。

**新增帳號後 Discord 沒有出現任何訊息**
→ 這是正常的：第一次檢查只會記下現有的貼文。那個帳號的下一篇新貼文就會被公告。

**"Bluesky didn't accept the login"（Bluesky 不接受登入）**
→ 應用程式密碼已被刪除或輸入錯誤。請建立一組新的（第 3 步之 1），並貼到 **Settings → Bluesky login**（設定 → Bluesky 登入）。

**Discord 沒有收到測試訊息**
→ Webhook 可能已在 Discord 中被刪除。請建立新的 Webhook（第 3 步之 2），並把網址貼到 **Settings → Discord channels → Edit**（設定 → Discord 頻道 → 編輯）。

**其他問題**
→ 指令檔會把報告存在 `logs\problem-report.txt`（密碼和 Webhook 連結會被隱藏）。尋求協助時請分享這個檔案。

---

## 從舊版本（黑色視窗版）升級

1. 關閉舊版機器人的黑色視窗。
2. 安裝 Docker Desktop（第 1 步）。
3. 在同一個資料夾按兩下 `setup.bat`，當它詢問是否要把帳號和設定搬過來時，回答 **Y**。

您舊的 `db.sqlite3` 和 `.env` 檔案只會被讀取。搬移之後，可以把它們留作備用，但請不要再啟動舊版本，否則每篇貼文都會被公告兩次。舊的指令檔在 `dev` 資料夾中，供開發者使用。

---

## 更多資訊

以下文件目前只有英文版：

- [docs/DOCKER.md](docs/DOCKER.md)：Docker 版本的運作方式、更新、備份、還原和發行。
- [docs/FRONTEND.md](docs/FRONTEND.md)：控制面板（開發者用）。
- [docs/API.md](docs/API.md)：機器人的 API（開發者用）。
- [CONTRIBUTING.zh-TW.md](CONTRIBUTING.zh-TW.md)：開發者指南（繁體中文）：專案結構、分開開發控制面板或後端、API 規格（`docs/openapi.yaml`）、測試和 CI。
- [CHANGELOG.md](CHANGELOG.md)：每個版本的變更內容。
- [SECURITY.md](SECURITY.md)：如何私下回報安全問題。

## 授權

這個機器人是採用 [MIT 授權](LICENSE)的免費開源軟體。您可以使用、複製、修改和分享它，包括商業用途，只要保留著作權聲明和授權聲明即可。本軟體不提供任何擔保。

它使用了其他開源軟體，這些軟體保有各自的授權：請見 [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)，控制面板中也有連結（Settings → About this bot）。本專案與 Bluesky 和 Discord 沒有任何關係。

> 授權的正式文字以英文的 [LICENSE](LICENSE) 檔案為準；中文說明僅供參考。
