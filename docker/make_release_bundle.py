"""The deployment files (Windows and Linux), and the downloads for non-technical users built from them.

    python docker/make_release_bundle.py --image ghcr.io/OWNER/bluesky-discord-alert-bot:1.0.0 --out dist
    python docker/make_release_bundle.py --image <repo>:1.0.0@sha256:<digest> --from-bundle bundle/windows --out dist
    python docker/make_release_bundle.py --platform linux --image <repo>:1.0.0@sha256:<digest> \
        --from-bundle bundle/linux --out dist                             # BlueskyDiscordAlertBot-1.0.0-linux.tar.gz
    python docker/make_release_bundle.py --export /app/bundle/windows     # the image build (Dockerfile)
    python docker/make_release_bundle.py --platform linux --export /app/bundle/linux

Linux: ./alertbot (docker/linux/alertbot in the repository), docker-compose.yml, README.txt,
README.zh-TW.txt, LICENSE and THIRD_PARTY_NOTICES.txt, at /app/bundle/linux. Its download is a
.tar.gz with everything in a bluesky-alert-bot/ folder, plus docker/release.env (the image pinned by
digest), never docker/compose.env: that file holds an install's own settings, which unpacking a
download again must not replace. ./alertbot setup creates it from docker/release.env.

The deployment files (bundle_files) are the replaceable, version-matched files: the six
scripts, docker/scripts/common.bat, docker-compose.yml, README.txt, README.zh-TW.txt, LICENSE
and THIRD_PARTY_NOTICES.txt. --export writes them, with bundle.json (each file's SHA-256), into
the image at /app/bundle/windows, where an updater can copy them out with `docker cp`
(no Git, Python or Node.js needed on Windows).

Never part of the deployment files: docker/compose.env (it pins the image of a release by its
digest, which can't be known inside that image) and anything local to an install (its port,
data, backups, logs, .env). The download adds compose.env, with ALERTBOT_IMAGE set.

The download is reproducible: entries sorted, fixed timestamps and permissions, so the same
files give a byte-identical zip (and the same SHA-256). --from-bundle builds it from files
copied out of the image, so the download and the image hold exactly the same files.
Used by .github/workflows/release.yml; see docs/DOCKER.md, "Releases", and docs/RELEASES.md.
"""
import argparse
import gzip
import hashlib
import io
import json
import re
import tarfile
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
   new folder, and delete the old one once you're happy: its files belong to the older
   version. (1.0.0-rc.1's start.bat would replace the bot with the older version, which
   can't use the updated data; if that happens, run start.bat in the new folder.)
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
   之後只用新的資料夾，確認沒問題後刪除舊的資料夾：那些檔案屬於舊版本。
   （1.0.0-rc.1 的 start.bat 會把機器人換回無法使用新資料的舊版本；
   如果發生了，請在新資料夾執行 start.bat。）之前的備份仍可用新資料夾裡的 restore.bat 還原。

你的帳號、設定和紀錄都存在 Docker Desktop 裡，這些指令檔絕對不會刪掉它們。
update.bat 和 backup.bat 做的備份會放在這裡的 backups 資料夾。

這是採用 MIT 授權的免費開源軟體（見 LICENSE，以英文原文為準）。機器人（這個資料夾
下載的 Docker 映像檔）也包含其他開源軟體，請見 THIRD_PARTY_NOTICES.txt。
完整說明請看專案的 README.zh-TW.md。
"""


LINUX_FILES = {"alertbot": "docker/linux/alertbot", "docker-compose.yml": "docker-compose.yml",
               "LICENSE": "LICENSE", "THIRD_PARTY_NOTICES.txt": "THIRD_PARTY_NOTICES.txt"}
EXECUTABLE = {"alertbot"}
LINUX_FOLDER = "bluesky-alert-bot"
RELEASE_ENV = "docker/release.env"
README_LINUX = """Bluesky to Discord Alert Bot for Linux servers (Ubuntu)
(繁體中文說明：README.zh-TW.txt)

Needs Docker Engine with its Compose plugin (2.23.0 or later). The full guide, with the
installation command: docs/UBUNTU.md in the project
(https://github.com/qazwsx08ghj/bluesky-discord-alert-bot/blob/main/docs/UBUNTU.md).

In this folder:
  ./alertbot setup     first-time setup (asks for the address and port; safe to run again)
  ./alertbot status    is it running, which version, where to open the control panel
  ./alertbot start | stop
  ./alertbot config    change the address and port of the control panel later
  ./alertbot update    install a newer version (backs up first; goes back by itself if it fails)
  ./alertbot backup    save a backup copy into the backups folder here
  ./alertbot restore   put a backup back (the current data is backed up first)

The control panel has no login. It listens on this server only (127.0.0.1) unless you choose
otherwise; reach it from your computer with an SSH tunnel (see the guide).

Your accounts, settings and history are kept in Docker (the volume alertbot_bot-data) and are
never deleted by these files. Backups also go into the backups folder here; they contain
passwords, so keep them private.

Free and open source under the MIT License (see LICENSE). The bot (the Docker image this
downloads) also contains other open-source software: see THIRD_PARTY_NOTICES.txt.
"""
README_LINUX_ZH_TW = """Bluesky to Discord Alert Bot（Linux 伺服器版，Ubuntu）
(English: README.txt)

需要 Docker Engine 和它的 Compose 外掛（2.23.0 或更新版本）。完整說明和安裝指令請看專案的
docs/UBUNTU.zh-TW.md
（https://github.com/qazwsx08ghj/bluesky-discord-alert-bot/blob/main/docs/UBUNTU.zh-TW.md）。

在這個資料夾裡：
  ./alertbot setup     第一次設定（會詢問位址和連接埠；重複執行也安全）
  ./alertbot status    是否在執行、哪個版本、控制面板的網址
  ./alertbot start | stop
  ./alertbot config    之後再修改控制面板的位址和連接埠
  ./alertbot update    更新到新版本（會先備份；失敗會自動退回）
  ./alertbot backup    備份到這裡的 backups 資料夾
  ./alertbot restore   還原備份（會先備份目前的資料）

控制面板沒有登入功能。除非你另外選擇，否則它只在這台伺服器上（127.0.0.1）提供服務；
請用 SSH 通道從你的電腦連線（見說明文件）。

你的帳號、設定和紀錄存在 Docker 裡（alertbot_bot-data 磁碟區），這些檔案絕對不會刪掉它們。
備份也會放在這裡的 backups 資料夾；備份含有密碼，請妥善保管。

這是採用 MIT 授權的免費開源軟體（見 LICENSE，以英文原文為準）。機器人（這個資料夾
下載的 Docker 映像檔）也包含其他開源軟體，請見 THIRD_PARTY_NOTICES.txt。
"""

BUNDLE_SCHEMA = "alertbot-bundle/1"  # release_meta/bundle.py reads what export() writes
INDEX = "bundle.json"
COMPOSE_ENV = "docker/compose.env"
IMAGE_REF = re.compile(r"(?:[a-z0-9.-]+(?::\d+)?/)?[a-z0-9._/-]+(?::[A-Za-z0-9._-]+)?(?:@sha256:[0-9a-f]{64})?")
# A fixed timestamp (the earliest a zip can hold) and permissions, so the zip is reproducible.
ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def bundle_files(root: Path = ROOT, platform: str = "windows") -> dict:
    """The deployment files: relative path (with /) -> exact bytes. Line endings come from
    .gitattributes (.bat files CRLF, the others LF), so they are the same on every computer."""
    if platform == "linux":
        files = {name: (root / source).read_bytes().replace(b"\r\n", b"\n") if name == "alertbot"
                 else (root / source).read_bytes() for name, source in LINUX_FILES.items()}
        files["README.txt"] = README_LINUX.encode("utf-8")
        files["README.zh-TW.txt"] = README_LINUX_ZH_TW.encode("utf-8")
        return dict(sorted(files.items()))
    files = {name: (root / name).read_bytes() for name in FILES}
    files["README.txt"] = README.replace("\n", "\r\n").encode("utf-8-sig")
    # With a BOM, so every version of Notepad shows the Chinese text correctly.
    files["README.zh-TW.txt"] = README_ZH_TW.replace("\n", "\r\n").encode("utf-8-sig")
    return dict(sorted(files.items()))


def index(files: dict, platform: str = "windows") -> dict:
    return {"schema": BUNDLE_SCHEMA, "platform": platform,
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}}


def export(dest: Path, root: Path = ROOT, platform: str = "windows") -> dict:
    """Write the deployment files and bundle.json into dest (the image build). Returns the index."""
    files = bundle_files(root, platform)
    for name, data in files.items():
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if name in EXECUTABLE else 0o644)
    info = index(files, platform)
    (dest / INDEX).write_text(json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    # The same hashes as plain "<sha256> <name>" lines, beside the folder, for update.bat (certutil)
    # and ./alertbot update (sha256sum) to check the files they copy out, without reading JSON.
    sums = "".join(f"{digest} {name}\n" for name, digest in sorted(info["files"].items()))
    dest.with_name(dest.name + ".sha256").write_text(sums, encoding="ascii", newline="\n")
    return info


def read_bundle(folder: Path, platform: str = "windows") -> dict:
    """The deployment files from a folder written by export() (for instance copied out of the
    image), checked against its bundle.json (release_meta.bundle.verify)."""
    import sys

    sys.path.insert(0, str(ROOT))
    from release_meta import MetadataError, bundle

    try:
        return bundle.read(folder, platform)
    except MetadataError as exc:
        raise SystemExit(f"The deployment files can't be used: {exc}") from None


def _version_of(image: str) -> str:
    last = image.split("@", 1)[0].rsplit("/", 1)[-1]
    return last.rsplit(":", 1)[1] if ":" in last else "latest"


def compose_env(image: str, root: Path = ROOT) -> bytes:
    """docker/compose.env of the download: the repository's template with ALERTBOT_IMAGE set."""
    env = (root / "docker" / "compose.env").read_text(encoding="utf-8")
    env, count = re.subn(r"(?m)^ALERTBOT_IMAGE=.*$", f"ALERTBOT_IMAGE={image}", env)
    assert count == 1, "docker/compose.env must have one ALERTBOT_IMAGE= line"
    return env.replace("\r\n", "\n").encode("utf-8")


def write_zip(target: Path, files: dict) -> Path:
    """A reproducible zip: sorted entries, fixed timestamps and permissions, fixed compression."""
    with zipfile.ZipFile(target, "w") as zf:
        for name in sorted(files):
            entry = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            entry.create_system = 3  # Unix: the permissions above are read the same everywhere
            zf.writestr(entry, files[name], compresslevel=9)
    return target


def write_tar(target: Path, files: dict) -> Path:
    """A reproducible .tar.gz, everything in LINUX_FOLDER/: sorted entries, fixed times, owners and
    permissions (./alertbot executable), and a gzip header without a time or name."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        folders = sorted({"/".join(n.split("/")[:i]) for n in files for i in range(1, n.count("/") + 1)})
        for name in [""] + folders:
            entry = tarfile.TarInfo(f"{LINUX_FOLDER}/{name}".rstrip("/"))
            entry.type, entry.mode, entry.mtime = tarfile.DIRTYPE, 0o755, 0
            tar.addfile(entry)
        for name in sorted(files):
            entry = tarfile.TarInfo(f"{LINUX_FOLDER}/{name}")
            entry.size, entry.mtime = len(files[name]), 0
            entry.mode = 0o755 if name in EXECUTABLE else 0o644
            tar.addfile(entry, io.BytesIO(files[name]))
    with open(target, "wb") as f, gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0, compresslevel=9) as gz:
        gz.write(raw.getvalue())
    return target


def make(image: str, out: Path, from_bundle: Path | None = None, platform: str = "windows") -> Path:
    # [registry[:port]/]name[:tag][@sha256:digest], e.g. ghcr.io/owner/bot:1.0.0 or localhost:5000/bot:stable
    if not IMAGE_REF.fullmatch(image):
        raise SystemExit(f"Not a valid image name: {image}")
    files = read_bundle(from_bundle, platform) if from_bundle else bundle_files(platform=platform)
    for never in (COMPOSE_ENV, RELEASE_ENV):
        if never in files:
            raise SystemExit(f"{never} is never a deployment file")
    out.mkdir(parents=True, exist_ok=True)
    if platform == "linux":
        files[RELEASE_ENV] = f"ALERTBOT_IMAGE={image}\n".encode("utf-8")
        return write_tar(out / f"BlueskyDiscordAlertBot-{_version_of(image)}-linux.tar.gz", files)
    files[COMPOSE_ENV] = compose_env(image)
    return write_zip(out / f"BlueskyDiscordAlertBot-{_version_of(image)}.zip", files)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", help="published image, e.g. ghcr.io/owner/bot:1.0.0 (a release: :1.0.0@sha256:...)")
    parser.add_argument("--out", default="dist", help="output folder")
    parser.add_argument("--from-bundle", type=Path, help="deployment files copied out of the image (with bundle.json)")
    parser.add_argument("--export", type=Path, help="write the deployment files and bundle.json here, and stop")
    parser.add_argument("--platform", choices=("windows", "linux"), default="windows")
    args = parser.parse_args()
    if args.export:
        info = export(args.export, platform=args.platform)
        print(f"{len(info['files'])} {args.platform} deployment files written to {args.export}")
    elif args.image:
        print(make(args.image, Path(args.out), from_bundle=args.from_bundle, platform=args.platform))
    else:
        parser.error("--image or --export is needed")
