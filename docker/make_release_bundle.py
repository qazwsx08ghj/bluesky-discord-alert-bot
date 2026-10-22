"""Make the download for non-technical users: the scripts plus a Compose file that uses a
published image, so nothing is built on their computer.

    python docker/make_release_bundle.py --image ghcr.io/OWNER/bluesky-discord-alert-bot:1.0.0 --out dist

Creates dist/BlueskyDiscordAlertBot-<version>.zip with:
    setup.bat  start.bat  update.bat  stop.bat  backup.bat  restore.bat
    docker-compose.yml  docker/compose.env (ALERTBOT_IMAGE set)  docker/scripts/common.bat
    README.txt  README.zh-TW.txt  LICENSE  THIRD_PARTY_NOTICES.txt
update.bat in that folder downloads newer images published under the same tag (use a
moving tag such as :stable for automatic updates, or a version tag to stay put).
Used by .github/workflows/release.yml; see docs/DOCKER.md, "Releases".
"""
import argparse
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["setup.bat", "start.bat", "update.bat", "stop.bat", "backup.bat", "restore.bat", "docker-compose.yml",
         "docker/scripts/common.bat", "LICENSE", "THIRD_PARTY_NOTICES.txt"]
README = """Bluesky to Discord Alert Bot
(繁體中文說明：README.zh-TW.txt)

Needs: Docker Desktop (https://www.docker.com/products/docker-desktop/). It has its own
license terms: if you use it for a business or another organisation, check that you qualify
for free use: https://docs.docker.com/subscription/desktop-license/

1. Keep this folder somewhere permanent, for example Documents.
2. Double-click setup.bat. The control panel opens in your browser when it's ready.
   If Windows says "Windows protected your PC", choose "More info", then "Run anyway"
   (the scripts are plain text you can read). To avoid the warning next time: before
   unzipping, right-click the .zip, choose Properties, tick "Unblock" and press OK.
3. Later: start.bat opens the control panel, update.bat installs a new version
   (after a backup), stop.bat stops the bot, backup.bat saves a backup copy,
   restore.bat puts a backup back (after backing up the current data).
4. Already using the bot, and this is a newer download (for example a test version, "rc")?
   Unzip it into a new folder and double-click update.bat in the new folder. It backs up
   your data first and keeps your accounts, settings and history. From then on use only the
   new folder and delete the old one: its files would start the older version again.
   Your backups stay available to restore.bat in the new folder.

Your accounts, settings and history are kept inside Docker Desktop and are never deleted
by these files. Backups made by update.bat and backup.bat also go into the backups folder here.

Free and open source under the MIT License (see LICENSE). The bot (the Docker image this
downloads) also contains other open-source software: see THIRD_PARTY_NOTICES.txt.
"""
README_ZH_TW = """Bluesky to Discord Alert Bot（繁體中文說明）
(English: README.txt)

需要先安裝 Docker Desktop（https://www.docker.com/products/docker-desktop/）。
Docker Desktop 有自己的授權條款，公司或組織使用的話，請先確認是否符合免費使用的資格：
https://docs.docker.com/subscription/desktop-license/

1. 把這個資料夾放在不會移動的地方，例如「文件」。
2. 點兩下 setup.bat。準備好之後，瀏覽器會打開控制面板（目前只有英文介面）。
   如果 Windows 顯示「Windows 已保護您的電腦」，請按「其他資訊」→「仍要執行」
   （這些指令檔都是可以直接打開來看的純文字檔）。下次想避免這個警告：解壓縮前在
   zip 檔上按右鍵 →「內容」，勾選「解除封鎖」再按「確定」。
3. 之後：start.bat 打開控制面板，update.bat 更新到新版本（會先備份），stop.bat 停止
   機器人，backup.bat 備份資料，restore.bat 還原備份（會先備份目前的資料）。
4. 已經在用這個機器人，而這是較新的下載（例如測試版「rc」）？請把它解壓縮到新的資料夾，
   然後點兩下新資料夾裡的 update.bat。它會先備份，你的帳號、設定和紀錄都會保留。
   之後只用新的資料夾，並刪除舊的資料夾：舊資料夾裡的檔案會再啟動舊版本。
   之前的備份仍可用新資料夾裡的 restore.bat 還原。

你的帳號、設定和紀錄都存在 Docker Desktop 裡，這些指令檔絕對不會刪掉它們。
update.bat 和 backup.bat 做的備份會放在這裡的 backups 資料夾。

這是採用 MIT 授權的免費開源軟體（見 LICENSE，以英文原文為準）。機器人（這個資料夾
下載的 Docker 映像檔）也包含其他開源軟體，請見 THIRD_PARTY_NOTICES.txt。
完整說明請看專案的 README.zh-TW.md。
"""


def make(image: str, out: Path) -> Path:
    # [registry[:port]/]name[:tag][@sha256:digest], e.g. ghcr.io/owner/bot:1.0.0 or localhost:5000/bot:stable
    if not re.fullmatch(r"(?:[a-z0-9.-]+(?::\d+)?/)?[a-z0-9._/-]+(?::[A-Za-z0-9._-]+)?(?:@sha256:[0-9a-f]{64})?", image):
        raise SystemExit(f"Not a valid image name: {image}")
    last = image.split("@", 1)[0].rsplit("/", 1)[-1]
    version = last.rsplit(":", 1)[1] if ":" in last else "latest"
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"BlueskyDiscordAlertBot-{version}.zip"
    env = (ROOT / "docker" / "compose.env").read_text(encoding="utf-8")
    env, count = re.subn(r"(?m)^ALERTBOT_IMAGE=.*$", f"ALERTBOT_IMAGE={image}", env)
    assert count == 1, "docker/compose.env must have one ALERTBOT_IMAGE= line"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in FILES:
            zf.write(ROOT / name, name)
        zf.writestr("docker/compose.env", env.replace("\r\n", "\n"))
        zf.writestr("README.txt", README.replace("\n", "\r\n").encode("utf-8-sig"))
        # With a BOM, so every version of Notepad shows the Chinese text correctly.
        zf.writestr("README.zh-TW.txt", README_ZH_TW.replace("\n", "\r\n").encode("utf-8-sig"))
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", required=True, help="published image, e.g. ghcr.io/owner/bot:1.0.0")
    parser.add_argument("--out", default="dist", help="output folder")
    args = parser.parse_args()
    print(make(args.image, Path(args.out)))
