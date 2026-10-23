"""What this image is: its version, database migrations, platform and deployment files. Read-only.

    docker run --rm <image>@sha256:<digest> describe        (docker/entrypoint.sh)
    python manage.py describe_release

Prints JSON (schema alertbot-describe/1). The release workflow (M2.3) runs it on the image it has
just pushed, by digest, and builds the release manifest from it (release_meta/generate.py), so
the manifest describes the image that was actually built. Touches no data: no database
connection (the migrations are read from the code), no network, nothing written.

    {"schema": "alertbot-describe/1",
     "version": "1.0.0",                   # ALERTBOT_VERSION, set when the image is built
     "version_is_release": true,           # a SemVer version (not "dev" or "ci")
     "platform": "linux/amd64",            # the platform of the image this runs in
     "data_schema": {"activity": "0004_...", "bluesky_monitor": "0009_...", ...},
     "bundles": {"windows": {"files": {"update.bat": "<sha256>", ...}}},
     "problems": []}

Exit code 0, or 1 when there is a problem (listed under "problems"): the deployment files are
missing or don't match their index, or an app has more than one newest migration.
"""
import json
import os
import platform
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.migrations.loader import MigrationLoader

from release_meta import MetadataError, bundle, semver

SCHEMA = "alertbot-describe/1"
ARCHITECTURES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def data_schema() -> tuple[dict, list]:
    """{app: newest migration} for this project's apps, read from the code; and any problems."""
    graph = MigrationLoader(None, ignore_no_migrations=True).graph
    # This project's apps: a top-level package that is a folder of the project (not Django's or a
    # library's, even when a development virtualenv sits inside the project folder).
    base = Path(settings.BASE_DIR).resolve()
    ours = {c.label for c in apps.get_app_configs()
            if "." not in c.name and (base / c.name).is_dir() and Path(c.path).resolve() == (base / c.name).resolve()}
    leaves, problems = {}, []
    for app, name in graph.leaf_nodes():
        if app not in ours:
            continue
        if app in leaves:
            problems.append(f"{app} has more than one newest migration ({leaves[app]}, {name})")
        leaves[app] = name
    return dict(sorted(leaves.items())), problems


def describe(bundle_dir: Path) -> dict:
    version = settings.APP_VERSION
    machine = platform.machine().lower()
    schema_map, problems = data_schema()
    result = {
        "schema": SCHEMA,
        "version": version,
        "version_is_release": semver.is_valid(version),
        "platform": f"{platform.system().lower()}/{ARCHITECTURES.get(machine, machine)}",
        "data_schema": schema_map,
        "bundles": {},
        "problems": problems,
    }
    try:
        result["bundles"]["windows"] = {"files": bundle.verify(bundle_dir / "windows")}
    except MetadataError as exc:
        problems.append(f"windows deployment files: {exc}")
    return result


class Command(BaseCommand):
    help = "Describe this image (version, migrations, platform, deployment files) as JSON. Read-only."

    def add_arguments(self, parser):
        parser.add_argument("--bundle-dir", default=os.getenv("ALERTBOT_BUNDLE_DIR", "/app/bundle"),
                            help="where the deployment files are (default /app/bundle)")

    def handle(self, *args, bundle_dir, **options):
        result = describe(Path(bundle_dir))
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        if result["problems"]:
            raise SystemExit(1)
