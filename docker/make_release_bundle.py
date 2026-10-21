"""Make the download for non-technical users: the scripts plus a Compose file that uses a
published image, so nothing is built on their computer.

    python docker/make_release_bundle.py --image ghcr.io/OWNER/bluesky-discord-alert-bot:1.0.0 --out dist

Creates dist/BlueskyDiscordAlertBot-<version>.zip with:
    setup.bat  start.bat  update.bat  stop.bat  backup.bat
    docker-compose.yml  docker/compose.env (ALERTBOT_IMAGE set)  docker/scripts/common.bat
    README.txt
update.bat in that folder downloads newer images published under the same tag (use a
moving tag such as :stable for automatic updates, or a version tag to stay put).
Used by .github/workflows/release.yml; see docs/DOCKER.md, "Releases".
"""
import argparse
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["setup.bat", "start.bat", "update.bat", "stop.bat", "backup.bat", "docker-compose.yml",
         "docker/scripts/common.bat"]
README = """Bluesky to Discord Alert Bot

Needs: Docker Desktop (https://www.docker.com/products/docker-desktop/).

1. Unzip this folder somewhere permanent, for example Documents.
2. Double-click setup.bat. The control panel opens in your browser when it's ready.
3. Later: start.bat opens the control panel, update.bat installs a new version
   (after a backup), stop.bat stops the bot, backup.bat saves a backup copy.

Your accounts, settings and history are kept inside Docker Desktop and are never deleted
by these files. Backups made by update.bat and backup.bat also go into the backups folder here.
"""


def make(image: str, out: Path) -> Path:
    if not re.fullmatch(r"[a-z0-9./_-]+(:[A-Za-z0-9._-]+)?(@sha256:[0-9a-f]{64})?", image):
        raise SystemExit(f"Not a valid image name: {image}")
    version = image.rsplit(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else "latest"
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"BlueskyDiscordAlertBot-{version}.zip"
    env = (ROOT / "docker" / "compose.env").read_text(encoding="utf-8")
    env, count = re.subn(r"(?m)^ALERTBOT_IMAGE=.*$", f"ALERTBOT_IMAGE={image}", env)
    assert count == 1, "docker/compose.env must have one ALERTBOT_IMAGE= line"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in FILES:
            zf.write(ROOT / name, name)
        zf.writestr("docker/compose.env", env.replace("\r\n", "\n"))
        zf.writestr("README.txt", README.replace("\n", "\r\n"))
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", required=True, help="published image, e.g. ghcr.io/owner/bot:1.0.0")
    parser.add_argument("--out", default="dist", help="output folder")
    args = parser.parse_args()
    print(make(args.image, Path(args.out)))
