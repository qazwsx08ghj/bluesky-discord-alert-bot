# 參與開發

[English](CONTRIBUTING.md) | **繁體中文**

這份文件寫給想修改機器人程式碼的人。如果你只是要使用機器人，看 [README](README.zh-TW.md) 就夠了，完全不需要安裝 Python、Node.js 或 Git。

> 內容與英文版 [CONTRIBUTING.md](CONTRIBUTING.md) 相同，有出入時以英文版為準。程式碼區塊裡的註解維持英文。

這個專案分成前後兩半，兩邊只透過 API 溝通。
- **後端：** Django 和 Django REST Framework，加上在背景執行的檢查程式（checker）。見 [docs/BACKEND.md](docs/BACKEND.md)。
- **前端：** `frontend/` 裡的 React 控制面板。見 [docs/FRONTEND.md](docs/FRONTEND.md)。
- **兩者之間的規格：** [`docs/openapi.yaml`](docs/openapi.yaml)，用 OpenAPI 3.1 描述每個 API 端點，由後端程式碼自動產生。前端的 TypeScript 型別再由它產生。[docs/API.md](docs/API.md) 用文字說明 API；確切的欄位和型別以這份規格為準。

你可以只開發其中一邊，不用安裝另一邊的工具。

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

需要 Node.js 20.19 以上（建議用 22）；想要有一個實際運作的示範機器人的話，還需要 Docker。

```
docker compose -f dev/docker-compose.yml up --build      # the demo bot at http://127.0.0.1:8765/
cd frontend
npm ci
set ALERTBOT_API=http://127.0.0.1:8765                   # PowerShell: $env:ALERTBOT_API="http://127.0.0.1:8765"
npm run dev                                              # the panel at http://localhost:5173, live reload
```

示範機器人用的是真正的後端，只是帳號都是虛構的，Bluesky 和 Discord 也是假的。它在設計上就很安全：
- 碰不到你的 `.env`、資料庫或任何資料夾；
- 它的容器完全連不上網路；
- 也碰不到真正機器人的容器或資料；
- 執行 `docker compose -f dev/docker-compose.yml down` 之後，所有東西都會清掉。

`DEMO_SCENARIO=empty` 會顯示第一次使用的畫面，`problem` 則是應用程式密碼被拒絕的狀況。「送到」Discord 的訊息可以在 http://127.0.0.1:8765/__demo__/discord/ 看到。

沒有 Docker 也能跑 `npm test`：它會在模擬的瀏覽器裡，用假的 API 測試整個控制面板。

型別：API 的資料結構不要自己手寫。`src/api/types.ts` 只是幫自動產生的 `src/api/schema.gen.ts` 取比較短的名稱。如果控制面板需要 API 還沒提供的東西，要先改後端（第 4 節）。

## 3. 只開發後端（不需要 Node.js）

需要 Python 3.11。

```
python -m venv venv
venv\Scripts\activate                      # macOS/Linux: source venv/bin/activate
pip install -r requirements-dev.txt        # the app's exact versions plus the contract tools
python manage.py test                      # every backend test, about 3 minutes
```

`dev\setup-without-docker.bat` 會做一樣的事，另外再建立資料庫。`requirements-dev.txt` 只多了開發工具（drf-spectacular、jsonschema、PyYAML），它們不會放進 Docker 映像檔，機器人執行時也用不到。

Django 的測試會用臨時的建置結果檢查控制面板，所以不需要 Node.js。想看到控制面板本身的話，可以用 Docker 安裝（`setup.bat`），或用 Node.js 建置一次（`cd frontend && npm ci && npm run build`）。

**開發時千萬不要用你的真實資料。** 測試用的都是臨時資料庫和假的登入資訊。其他情況請把 `DATABASE_PATH` 指到一個暫存檔，或改用示範伺服器：`python config\testing\demo_server.py`。

## 4. 修改 API

規格是從程式碼產生的，前端型別又是從規格產生的。這兩個檔案都會 commit，所以只要 API 有變動，review 時就會看到 `docs/openapi.yaml` 跟著改。

1. 修改 view 或 serializer，並加上測試。
2. 如果新增或修改了欄位、回應或錯誤，請在 `config/openapi.py` 裡描述。請求和回應的結構都寫在那裡、對應的 view 旁邊；真正的 view 不需要動。
3. 重新產生並檢查：
   ```
   python manage.py api_contract --write     # rewrites docs/openapi.yaml
   python manage.py test --tag contract      # every API answer matches the contract
   cd frontend
   npm run api:types                         # rewrites src/api/schema.gen.ts
   npm run typecheck                         # shows every place in the panel affected
   ```
4. 更新 `docs/API.md`（文字說明）和 `frontend/src/test/fakeApi.ts` 裡的假 API。假 API 的回應用 `satisfies` 對照自動產生的型別，所以不會不知不覺跟規格對不上。
5. 程式碼、`docs/openapi.yaml` 和 `schema.gen.ts` 要在同一個 commit 一起提交。

**規則：**
- **維持原本的行為。** 不要為了讓規格好寫就改 API 的回應，規格要描述它實際的樣子。規格和程式碼對不上時，符合性測試會失敗；這時請修正規格的描述，除非真的是程式碼有 bug。
- **不要弄壞用戶端。** 新增欄位沒問題；但移除、改名或改型別會讓用戶端（控制面板和未來的手機 App）壞掉，請先討論。
- **自動產生的檔案不要手動改。** 檢查時會重新產生一次，只要有一點差異就會失敗。

`python manage.py api_contract`（不加 `--write`）和 `npm run api:check` 只做檢查，檔案沒更新就會失敗，CI 兩個都會跑。不管在哪台電腦產生，結果都一模一樣：有排序、用 LF 換行、工具版本固定。

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
- 不會讀取或寫入你的 `.env`。

請維持這個原則：只要測試有可能連到真正的服務或真實資料，就算是 bug。

## 6. 持續整合（CI）

每次 push 到 `main` 或開 pull request 時，`.github/workflows/ci.yml` 都會執行，分成幾個獨立的工作：

| 工作 | 執行環境 | 檢查內容 |
|---|---|---|
| Backend（後端） | Linux | `pip-audit`、`manage.py check`、缺少的 migration、Django 測試（規格測試除外） |
| API contract and notices（API 規格與授權聲明） | Linux | `api_contract`、`test --tag contract`、`npm run api:check`、typecheck、`THIRD_PARTY_NOTICES.txt` 是否為最新 |
| Frontend（前端） | Linux | `npm audit`、typecheck、`npm test`、建置 |
| Browser tests（瀏覽器測試） | Linux | 用 Playwright 測試示範伺服器 |
| Windows scripts（Windows 指令檔） | Windows | 在 cmd.exe 中執行真正的 `.bat` 檔，搭配假的 Docker |
| Docker | Linux | Compose 檔案、建置映像檔（不推送）、映像檔內容、示範環境的安全性 |

`release.yml` 會先跑完上面所有工作，只要有一個失敗，就不會發行任何東西。

## 7. 基本原則

- **使用者永遠不需要開發工具。** 請保持一鍵執行的 Windows 指令檔、Docker 架構，以及只能從 `127.0.0.1` 存取的設計。
- **絕對不刪使用者的資料。** 指令檔不會移除 Docker volume，資料庫有變動前一定先備份，這兩點都有測試把關。
- **機密資訊不能外流。**
  - Webhook 網址和密碼不能出現在 API 回應、紀錄檔或頁面上。
  - 只能顯示遮蔽過的樣子。
  - 不要 commit `.env`、資料庫或備份。
- **版本要固定。** Python 套件的版本寫在 `requirements-lock.txt`（和 `requirements-dev.txt`），Node.js 套件寫在 `package-lock.json`。更新套件後請跑一次安全性檢查（audit）。

## 8. 授權

本專案採用 MIT 授權（[LICENSE](LICENSE)）。你貢獻的程式碼，也會以相同的授權釋出。

新增相依套件時：
- **先確認授權。** MIT、BSD、ISC 和 Apache-2.0 都沒問題。
- **會跟著 Docker 映像檔或控制面板一起發給使用者的套件，如果是 copyleft 授權（GPL、AGPL、LGPL）或條款比較特殊，請先討論。** 只在開發時用的工具影響比較小，因為它們不會被發行出去。
- **字型、圖示和圖片**要用允許再散布的授權，例如 SIL Open Font License 或 CC0。
- **重新產生授權聲明。** `THIRD_PARTY_NOTICES.txt` 收錄了 Docker 映像檔和控制面板裡所有第三方軟體的授權和著作權聲明，內容是從已安裝的套件讀出來的。改了 `requirements-lock.txt`、`requirements-docker.txt` 或控制面板的相依套件後，請先裝好確切的版本，再執行 `python docker/third_party_notices.py --write`。檔案沒更新的話，CI、Django 測試和 Docker 建置都會失敗。
- **沒附授權檔案的套件**，要把它的授權放到 `docker/notices/<name>-<version>.txt`，內容從該版本的官方來源複製（見 `docker/notices/README.md`）。

## 9. Pull request、變更紀錄與翻譯

- **Pull request** 請用 `.github/PULL_REQUEST_TEMPLATE.md` 的範本。一個 pull request 只做一件事，並說明你怎麼測試。
- **變更紀錄。** 使用者或 API 用戶端會注意到的變更，請在 [CHANGELOG.md](CHANGELOG.md) 的 `[Unreleased]` 底下加一行。內部整理不用寫。
- **中英兩種語言。** `README.md` 和 `CONTRIBUTING.md` 都有繁體中文版（`*.zh-TW.md`）。改了其中一份，請在同一個 pull request 裡一起改另一份；不會寫中文的話，在 pull request 裡說一聲，維護者會幫忙更新。測試會檢查兩個版本的章節、連結和指令是否一致。用語請參考 [docs/GLOSSARY.zh-TW.md](docs/GLOSSARY.zh-TW.md)。其他文件只有英文版。
- **安全性問題**請不要在公開的 issue 回報，做法請看 [SECURITY.md](SECURITY.md)。
