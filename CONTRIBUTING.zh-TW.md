# 參與開發

[English](CONTRIBUTING.md) | **繁體中文**

這份文件是給修改機器人程式碼的人看的。只使用機器人的人只需要看 [README](README.zh-TW.md)，他們永遠不需要安裝 Python、Node.js 或 Git。

> 本文件與英文版 [CONTRIBUTING.md](CONTRIBUTING.md) 內容相同；如有出入，以英文版為準。程式碼區塊中的註解保留英文。

這個專案分成兩半，兩者只在一個地方交會：API。
- **後端：** Django 和 Django REST Framework，加上在背景執行的檢查程式（checker）。見 [docs/BACKEND.md](docs/BACKEND.md)。
- **前端：** `frontend/` 裡的 React 控制面板。見 [docs/FRONTEND.md](docs/FRONTEND.md)。
- **兩者之間的規格：** [`docs/openapi.yaml`](docs/openapi.yaml)，用 OpenAPI 3.1 描述每個 API 端點，由後端程式碼自動產生。前端的 TypeScript 型別再由它產生。[docs/API.md](docs/API.md) 用文字說明 API；確切的欄位和型別以這份規格為準。

您可以只開發其中一半，不需要安裝另一半的工具。

## 1. 專案結構

```
activity/            activity log, problems, GET /api/status/
bluesky_monitor/     watched accounts, Bluesky login, the checker (scheduler, poller), Check All jobs
discord_alerts/      Discord channels (webhooks) and sending
botconfig/           settings stored in the database; backup, restore and start-up commands
config/              settings, URLs, security, serving the panel, the API contract (openapi.py)
  testing/           the demo server (fake Bluesky and Discord) and fakes for the script tests
frontend/            the React control panel (built into the Docker image)
docs/                documentation, openapi.yaml (generated), screenshots
docker/              the Docker entry point, the scripts' shared code, the release download builder
dev/                 for developers only: the demo stack in Docker, and running without Docker
*.bat                the Windows scripts users run (setup, start, update, stop, backup, restore)
```

| 資料夾 | 內容 |
|---|---|
| `activity/` | 活動紀錄、問題、`GET /api/status/` |
| `bluesky_monitor/` | 追蹤的帳號、Bluesky 登入、檢查程式（排程、輪詢）、「全部檢查」工作 |
| `discord_alerts/` | Discord 頻道（Webhook）與發送 |
| `botconfig/` | 存在資料庫中的設定；備份、還原與啟動指令 |
| `config/` | 設定、網址、安全性、提供控制面板、API 規格（`openapi.py`）；`testing/` 是示範伺服器與測試用的假元件 |
| `frontend/` | React 控制面板（建置後包進 Docker 映像檔） |
| `docs/` | 文件、`openapi.yaml`（自動產生）、螢幕截圖 |
| `docker/` | Docker 進入點、指令檔共用程式、發行下載檔的建置工具 |
| `dev/` | 僅供開發者：Docker 示範環境，以及不使用 Docker 的執行方式 |
| `*.bat` | 使用者執行的 Windows 指令檔（setup、start、update、stop、backup、restore） |

## 2. 只開發前端（不需要 Python）

您需要 Node.js 20.19 以上（建議 22）；如果想要一個執行中的示範機器人，還需要 Docker。

```
docker compose -f dev/docker-compose.yml up --build      # the demo bot at http://127.0.0.1:8765/
cd frontend
npm ci
set ALERTBOT_API=http://127.0.0.1:8765                   # PowerShell: $env:ALERTBOT_API="http://127.0.0.1:8765"
npm run dev                                              # the panel at http://localhost:5173, live reload
```

示範機器人是真正的後端，搭配虛構的帳號、假的 Bluesky 和假的 Discord。它的設計本身就是安全的：
- 它無法存取您的 `.env`、您的資料庫或您的任何資料夾；
- 它的容器沒有網路連線；
- 它碰不到真正機器人的容器或資料；
- 執行 `docker compose -f dev/docker-compose.yml down` 之後，所有東西都會消失。

`DEMO_SCENARIO=empty` 會顯示第一次使用的畫面；`problem` 會顯示應用程式密碼被拒絕的狀況。「發送」到 Discord 的訊息列在 http://127.0.0.1:8765/__demo__/discord/。

沒有 Docker 的話，`npm test` 仍然可以執行：它會在模擬瀏覽器中，用假的 API 測試整個控制面板。

型別：絕對不要手寫 API 的資料結構。`src/api/types.ts` 只是替自動產生的 `src/api/schema.gen.ts` 取簡短的名稱。如果控制面板需要 API 沒有提供的東西，要先修改後端（第 4 節）。

## 3. 只開發後端（不需要 Node.js）

您需要 Python 3.11。

```
python -m venv venv
venv\Scripts\activate                      # macOS/Linux: source venv/bin/activate
pip install -r requirements-dev.txt        # the app's exact versions plus the contract tools
python manage.py test                      # every backend test, about 3 minutes
```

`dev\setup-without-docker.bat` 會做同樣的事，並建立資料庫。`requirements-dev.txt` 只多加了開發工具（drf-spectacular、jsonschema、PyYAML）；它們永遠不會進入 Docker 映像檔，機器人執行時也不會用到。

Django 的測試會用暫時的建置結果來檢查控制面板的提供方式，所以不需要 Node.js。如果想看到控制面板本身，請使用 Docker 安裝（`setup.bat`），或用 Node.js 建置一次（`cd frontend && npm ci && npm run build`）。

**開發時絕對不要使用您的真實資料。** 測試使用暫時的資料庫和假的登入資訊。其他情況請把 `DATABASE_PATH` 指向一個暫存檔案，或使用示範伺服器：`python config\testing\demo_server.py`。

## 4. 修改 API

規格由程式碼產生，前端的型別再由規格產生。這兩個檔案都會提交到版本控制，所以 API 的任何變更在審查時都會顯示為 `docs/openapi.yaml` 的變更。

1. 修改 view 或 serializer，並附上測試。
2. 如果變更新增或修改了欄位、回應或錯誤，請在 `config/openapi.py` 中描述它。請求和回應的結構都記錄在那裡，緊鄰對應的 view；真正的 view 不會被修改。
3. 重新產生並檢查：
   ```
   python manage.py api_contract --write     # rewrites docs/openapi.yaml
   python manage.py test --tag contract      # every API answer matches the contract
   cd frontend
   npm run api:types                         # rewrites src/api/schema.gen.ts
   npm run typecheck                         # shows every place in the panel affected
   ```
4. 更新 `docs/API.md`（文字說明），以及 `frontend/src/test/fakeApi.ts` 中的假 API。它的回應用 `satisfies` 搭配自動產生的型別，所以不會在不知不覺中與規格脫節。
5. 把程式碼、`docs/openapi.yaml` 和 `schema.gen.ts` 一起提交。

**規則：**
- **保留既有行為。** 不要為了讓規格比較簡單就改變 API 的回應：要描述它實際的行為。如果規格和程式碼不一致，符合性測試會失敗；請修正描述，除非是程式碼有錯。
- **不要破壞用戶端。** 新增欄位沒問題。移除或重新命名欄位、或改變型別，會破壞用戶端（控制面板和未來的行動應用程式）；請先討論。
- **不要手動編輯自動產生的檔案。** 檢查會重新產生它們，只要有任何差異就會失敗。

`python manage.py api_contract`（不加 `--write`）和 `npm run api:check` 只做檢查，檔案過期時會失敗。CI 兩個都會執行。輸出在每台電腦上都完全相同：有排序、使用 LF 換行、工具版本固定。

## 5. 測試

| 指令 | 檢查內容 | 需要 |
|---|---|---|
| `python manage.py test` | 所有後端測試：API、檢查程式、發送、安全性、提供控制面板、規格、Windows 指令檔（在 Windows 上） | Python |
| `python manage.py test --tag contract` | 每個操作的實際回應是否符合 `docs/openapi.yaml`（物件是封閉的：不能有未記錄的欄位），以及檔案是否為最新 | Python |
| `python manage.py api_contract` | `docs/openapi.yaml` 是否與程式碼一致 | Python |
| `python docker/third_party_notices.py` | `THIRD_PARTY_NOTICES.txt` 是否與實際發行的套件版本一致 | Python、`npm ci` |
| `npm test`（在 `frontend/` 中） | 在模擬瀏覽器中，用假 API 測試控制面板 | Node.js |
| `npm run typecheck`、`npm run api:check` | TypeScript，以及 `schema.gen.ts` 是否與規格一致 | Node.js |
| `npm run e2e` | 用 Chromium 測試真正的應用程式，搭配假的 Bluesky 和 Discord | Node.js、Python |
| `RUN_DOCKER_TESTS=1` + `manage.py test config.tests_docker` | 在 Windows 上測試真正的指令檔和容器 | Docker Desktop |
| `RUN_DOCKER_TESTS=1` + `manage.py test config.tests_dev_stack` | 示範環境：沒有檔案、登入資訊或網路；假的 Discord | Docker |

**每個測試都是隔離的：**
- 假的登入資訊；
- 暫時的資料庫和資料夾；
- 不連到真正的 Bluesky 或 Discord；
- 不讀取也不寫入您的 `.env`。

請維持這個原則：任何可能連到真正服務或真實資料的測試都是錯誤。

## 6. 持續整合（CI）

`.github/workflows/ci.yml` 會在每次推送到 `main` 和每個 pull request 時執行，分成幾個獨立的工作：

| 工作 | 執行環境 | 檢查內容 |
|---|---|---|
| Backend（後端） | Linux | `pip-audit`、`manage.py check`、缺少的 migration、Django 測試（規格測試除外） |
| API contract and notices（API 規格與授權聲明） | Linux | `api_contract`、`test --tag contract`、`npm run api:check`、typecheck、`THIRD_PARTY_NOTICES.txt` 是否為最新 |
| Frontend（前端） | Linux | `npm audit`、typecheck、`npm test`、建置 |
| Browser tests（瀏覽器測試） | Linux | 用 Playwright 測試示範伺服器 |
| Windows scripts（Windows 指令檔） | Windows | 在 cmd.exe 中執行真正的 `.bat` 檔，搭配假的 Docker |
| Docker | Linux | Compose 檔案、建置映像檔（不推送）、映像檔內容、示範環境的安全性 |

`release.yml` 會先執行以上全部工作，只要有任何一個失敗，就什麼都不會發行。

## 7. 基本原則

- **使用者永遠不需要開發工具。** 請維持一鍵執行的 Windows 指令檔、Docker 架構，以及只能從 `127.0.0.1` 存取。
- **絕不刪除使用者的資料。** 指令檔永遠不會移除 Docker volume。每次資料庫變更前都會先備份。兩者都有測試檢查。
- **機密資訊必須保密。**
  - Webhook 網址和密碼絕不會出現在 API 回應、紀錄檔或頁面中。
  - 只會顯示遮蔽後的形式。
  - 絕不提交 `.env`、資料庫或備份。
- **固定版本。** Python 套件的版本在 `requirements-lock.txt`（以及 `requirements-dev.txt`）；Node.js 套件的版本在 `package-lock.json`。更新後請執行安全性稽核。

## 8. 授權

本專案採用 MIT 授權（[LICENSE](LICENSE)）。貢獻程式碼即表示您同意您的貢獻以相同的授權釋出。

新增相依套件時：
- **先確認它的授權。** MIT、BSD、ISC 和 Apache-2.0 都沒問題。
- **如果它會隨 Docker 映像檔或建置後的控制面板發給使用者，加入任何 copyleft 授權（GPL、AGPL、LGPL）或條款特殊的套件前，請先詢問。** 僅供開發使用的工具影響較小，因為它們不會被發行。
- **字型、圖示和圖片**需要允許再散布的授權，例如 SIL Open Font License 或 CC0。
- **重新產生授權聲明。** `THIRD_PARTY_NOTICES.txt` 收錄了 Docker 映像檔和控制面板所發行的所有軟體的授權和著作權聲明，內容讀取自已安裝的套件。修改 `requirements-lock.txt`、`requirements-docker.txt` 或控制面板的相依套件之後，請安裝確切的版本，並執行 `python docker/third_party_notices.py --write`。檔案過期時，CI、Django 測試和 Docker 建置都會失敗。
- **沒有附授權檔案的套件**，需要把它的授權放在 `docker/notices/<name>-<version>.txt`，內容複製自該版本的官方來源（見 `docker/notices/README.md`）。

## 9. Pull request、變更紀錄與翻譯

- **Pull request** 請使用 `.github/PULL_REQUEST_TEMPLATE.md` 中的範本。每個 pull request 只做一件事，並說明您如何測試。
- **變更紀錄。** 任何使用者或 API 用戶端會注意到的變更，請在 [CHANGELOG.md](CHANGELOG.md) 的 `[Unreleased]` 下加一行。內部整理不需要。
- **兩種語言。** `README.md` 和 `CONTRIBUTING.md` 有繁體中文版（`*.zh-TW.md`）。修改其中一份時，請在同一個 pull request 中修改另一份；如果您不會寫中文，請在 pull request 中說明，維護者會協助更新。測試會檢查兩個版本的章節、連結和指令是否一致。用語請參考 [docs/GLOSSARY.zh-TW.md](docs/GLOSSARY.zh-TW.md)。其他文件只有英文版。
- **安全性問題**絕不能在公開的 issue 中回報：請見 [SECURITY.md](SECURITY.md)。
