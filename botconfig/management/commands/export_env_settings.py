from django.core.management.base import BaseCommand

from botconfig import store


class Command(BaseCommand):
    help = ("Write the saved settings into .env, so an older version of the bot (before step 3) "
            "uses them. Run this before rolling back if the control panel reported a .env problem.")

    def handle(self, *args, **options):
        if store.env_path() is None:
            self.stderr.write("SETTINGS_ENV_MIRROR_PATH is empty, so there is no .env file to write.")
            raise SystemExit(1)
        ok, error = store.sync_env_file(force=True)
        if not ok:
            self.stderr.write(error)
            raise SystemExit(1)
        self.stdout.write("Settings (version %d) written to %s" % (store.get().version, store.env_path()))
