"""After the trial update (docker/entrypoint.sh `trial`: prepare_data, then this): does this version
work on the data? Django's checks, and /api/health/ through the test client (no port, no network):
it must answer 200, report this version, and have no migration left. Exit code 1 otherwise."""
import json

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.test import Client


class Command(BaseCommand):
    help = "Self-test of this version on the bot's data (no network)."

    def handle(self, *args, **options):
        call_command("check", stdout=self.stdout)
        response = Client(HTTP_HOST="localhost").get("/api/health/")
        try:
            body = json.loads(response.content)
        except ValueError:
            body = {}
        if response.status_code != 200 or body.get("version") != settings.APP_VERSION \
                or body.get("migrations_pending") != 0:
            raise CommandError(f"The self-test failed: health answered {response.status_code} "
                               f"{str(body)[:200]}")
        self.stdout.write(f"Self-test passed: version {settings.APP_VERSION} works on this data.")
