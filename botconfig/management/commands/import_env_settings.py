from django.core.management.base import BaseCommand

from botconfig import store


class Command(BaseCommand):
    help = ("Copy the settings from .env into the database. Does nothing if the database already "
            "has settings, unless --force is given. Secret values are never printed.")

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true",
                            help="Replace the saved settings with the .env values. A blank .env password "
                                 "keeps the saved one.")

    def handle(self, *args, force=False, **options):
        try:
            row, imported = store.import_from_environment(force=force)
        except store.SettingsSaveError as exc:
            self.stderr.write(str(exc))
            raise SystemExit(1)
        if not imported:
            self.stdout.write("The database already has settings (version %d); nothing was changed. "
                              "Use --force to replace them with the .env values." % row.version)
            return
        self.stdout.write("Settings are in the database (version %d): %s"
                          % (row.version, store._describe({f: getattr(row, f) for f in store.FIELDS})))
