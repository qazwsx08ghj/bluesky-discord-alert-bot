"""Generate or check the API contract, docs/openapi.yaml (a development tool).

    python manage.py api_contract           # check: is docs/openapi.yaml up to date? (CI runs this)
    python manage.py api_contract --write   # regenerate it after changing the API

Needs the development tools: pip install -r requirements-dev.txt. The descriptions of the
endpoints are in config/openapi.py. After --write, regenerate the frontend's types too
(cd frontend && npm run api:types). See CONTRIBUTING.md, "Changing the API".
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

CONTRACT = Path(settings.BASE_DIR) / "docs" / "openapi.yaml"


class Command(BaseCommand):
    help = "Check (default) or regenerate (--write) the API contract docs/openapi.yaml."

    def add_arguments(self, parser):
        parser.add_argument("--write", action="store_true", help="Regenerate docs/openapi.yaml.")

    def handle(self, *args, write=False, **options):
        try:
            from config.openapi import generate
        except ImportError as exc:
            raise CommandError(f"The contract tools aren't installed ({exc.name}). Run: "
                               "pip install -r requirements-dev.txt") from exc
        text, problems = generate()
        if problems:
            raise CommandError("drf-spectacular reported warnings or errors (shown above). Describe the "
                               "endpoint in config/openapi.py so the contract is complete.")
        if write:
            CONTRACT.write_bytes(text.encode("utf-8"))
            self.stdout.write(f"Wrote {CONTRACT.relative_to(settings.BASE_DIR)}. Now run: "
                              "cd frontend && npm run api:types")
            return
        current = CONTRACT.read_bytes().decode("utf-8").replace("\r\n", "\n") if CONTRACT.exists() else ""
        if current != text:
            raise CommandError(
                "docs/openapi.yaml is out of date: the API changed without the contract being regenerated. "
                "Run: python manage.py api_contract --write, then: cd frontend && npm run api:types, "
                "and commit both files.")
        self.stdout.write("docs/openapi.yaml is up to date.")
