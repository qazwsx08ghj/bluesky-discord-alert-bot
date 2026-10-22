"""Can this version use the bot's data? (used by the scripts before they start or replace the bot)

    python manage.py check_compat

Exit code 0: yes (also when there is no data yet). Exit code 3: a newer version updated the
data last, so this version must not run on it (see botconfig/compat.py). Only reads.
"""
from django.core.management.base import BaseCommand

from botconfig import compat


class Command(BaseCommand):
    help = "Exit 3 if the bot's data was last updated by a newer version of the bot."

    def handle(self, *args, **options):
        newer = compat.unknown_migrations()
        if newer:
            self.stderr.write(compat.explanation(newer))
            raise SystemExit(compat.NEWER_DATA_EXIT)
        self.stdout.write("This version can use the data.")
