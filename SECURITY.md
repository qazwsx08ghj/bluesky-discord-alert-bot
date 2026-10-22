# Security policy

[繁體中文摘要](#繁體中文摘要) below.

## Supported versions

Only the **latest release** receives security fixes. To get a fix, update with `update.bat` (it backs up first).

| Version | Security fixes |
|---|---|
| Latest release | Yes |
| Anything older | No: please update |

## Reporting a vulnerability

**Please don't report security problems in public issues, discussions or pull requests.**

Report them privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**. Only the maintainers can see the report.

Please include:
- the version (Settings → About this bot in the control panel);
- what an attacker could do, and the steps to reproduce it;
- whether it needs anything unusual, such as the panel being reachable from another computer.

**Never include real secrets:** no App Passwords, webhook URLs, `.env` files, databases or backups. Use made-up values. If you exposed a real secret by mistake, delete it at its source: a new App Password in Bluesky, or a new webhook in Discord.

## What to expect

This is a volunteer project, so these are goals, not guarantees:
- an acknowledgement **within 7 days**;
- an assessment, and a plan if the problem is confirmed, within about 30 days;
- a fix in a new release, credited to you if you wish, and published as a GitHub security advisory once the fix is out.

## Scope

The bot runs on the owner's own computer. Its control panel has no login, because it only accepts connections from that computer (`127.0.0.1`). Within that design, these are security problems:
- App Passwords, webhook URLs or other secrets appearing in API answers, pages, logs, problem reports or error pages;
- another website, or another computer, being able to read from or change the bot through the control panel or its API (for example by getting around the CSRF, Origin or host-name checks);
- the scripts, updates or restores deleting or damaging the user's data;
- private files (`.env`, databases, backups) ending up in the Docker image, the release download, or files the panel serves;
- the development stack (`dev/docker-compose.yml`) being able to reach the internet or the user's real data.

Out of scope:
- a user choosing to expose the panel to a network (for example by changing the port binding), since it has no login by design;
- someone who already controls the computer or its user account;
- problems in Docker Desktop, Bluesky or Discord themselves: please report those to them. A vulnerable dependency that we ship is in scope.

## 繁體中文摘要

- 只有**最新版本**會收到安全性修正。請用 `update.bat` 更新（會先自動備份）。
- **安全性問題請不要發在公開的 issue、討論區或 pull request。** 請到 GitHub 專案的 **Security** 分頁，按 **Report a vulnerability** 私下回報，只有維護者看得到。
- 請附上版本（控制面板的 Settings → About this bot）、攻擊者可以做到什麼，以及重現的步驟。
- **千萬不要附上真的機密資訊**：應用程式密碼、Webhook 網址、`.env`、資料庫和備份都不行，請改用虛構的值。萬一不小心外流了，請到來源把它作廢：在 Bluesky 刪掉那組應用程式密碼，或在 Discord 刪掉那個 Webhook，再重新建立一個。
- 這是志工維護的專案：我們會盡量在 **7 天內**回覆、約 30 天內完成評估。確認是問題的話，會在新版本修正，並在修正版發行後公開安全性公告。
- 屬於安全性問題的包括：機密資訊外流、其他網站或電腦能透過控制面板或 API 讀取或修改機器人、指令檔造成資料遺失、私人檔案被放進映像檔或下載檔，以及開發用的示範環境能連上網路。不在範圍內的有：使用者自己把控制面板開放到網路上、已經控制了這台電腦的人，以及 Docker Desktop、Bluesky 或 Discord 本身的問題。

內容如有出入，以英文版為準。
