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

Needs: Docker Desktop (https://www.docker.com/products/docker-desktop/).
Docker Desktop has its own license: free for personal use, education, non-commercial open
source and small businesses (under 250 employees and under US$10 million annual revenue);
larger organisations and government bodies need a paid Docker subscription. Details:
https://docs.docker.com/subscription/desktop-license/

0. Before unzipping: right-click the downloaded .zip, choose Properties, tick "Unblock" if it's
   there, and press OK. If Windows still says "Windows protected your PC" when you start a
   script, choose "More info", then "Run anyway". The scripts are plain text you can read.
1. Unzip this folder somewhere permanent, for example Documents.
2. Double-click setup.bat. The control panel opens in your browser when it's ready.
3. Later: start.bat opens the control panel, update.bat installs a new version
   (after a backup), stop.bat stops the bot, backup.bat saves a backup copy,
   restore.bat puts a backup back (after backing up the current data).

Your accounts, settings and history are kept inside Docker Desktop and are never deleted
by these files. Backups made by update.bat and backup.bat also go into the backups folder here.

Free and open source under the MIT License (see LICENSE). The bot (the Docker image this
downloads) also contains other open-source software: see THIRD_PARTY_NOTICES.txt.
"""
README_ZH_TW = """Bluesky to Discord Alert Bot（繁體中文說明）
(English: README.txt)

需要：Docker Desktop（https://www.docker.com/products/docker-desktop/）。
Docker Desktop 有它自己的授權條款：個人使用、教育用途、非商業開源專案，以及小型企業
（員工少於 250 人而且年營收少於 1,000 萬美元）可以免費使用；規模較大的組織和政府機關
需要購買 Docker 的付費訂閱。詳見：
https://docs.docker.com/subscription/desktop-license/

0. 解壓縮之前：在下載的 .zip 檔上按右鍵，選擇「內容」，如果有「解除封鎖」就勾選它，
   再按「確定」。如果執行指令檔時 Windows 仍然顯示「Windows 已保護您的電腦」，
   請選擇「其他資訊」，再選「仍要執行」。這些指令檔都是可以直接閱讀的純文字檔。
1. 把這個資料夾解壓縮到固定的位置，例如「文件」資料夾。
2. 按兩下 setup.bat。準備好之後，控制面板會在瀏覽器中開啟（控制面板目前只有英文介面）。
3. 之後：start.bat 開啟控制面板，update.bat 安裝新版本（會先備份），stop.bat 停止機器人，
   backup.bat 儲存備份，restore.bat 還原備份（會先備份目前的資料）。

您的帳號、設定和紀錄都保存在 Docker Desktop 裡，這些檔案絕不會刪除它們。
update.bat 和 backup.bat 製作的備份也會存在這裡的 backups 資料夾。

這是採用 MIT 授權的免費開源軟體（見 LICENSE，以英文原文為準）。機器人（這個資料夾
所下載的 Docker 映像檔）也包含其他開源軟體：見 THIRD_PARTY_NOTICES.txt。
完整說明：專案的 README.zh-TW.md。
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
