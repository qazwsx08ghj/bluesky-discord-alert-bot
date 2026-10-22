"""Django serves the built React control panel (config/frontend.py).

Every test uses its own temporary build folder, so the results don't depend on whether
`npm run build` was run on this machine. The real build is covered by the browser tests
(frontend/e2e) and the Docker tests (config/tests_docker.py).
"""
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings


INDEX = ('<!doctype html><html><head><title>Alert Bot · Bluesky → Discord</title>'
         '<script type="module" src="/assets/index-abc123.js"></script></head><body><div id="root"></div></body></html>')


class BuiltPanelMixin:
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dist = Path(tmp.name) / "dist"
        (self.dist / "assets").mkdir(parents=True)
        (self.dist / "index.html").write_text(INDEX, encoding="utf-8")
        (self.dist / "assets" / "index-abc123.js").write_text("console.log('panel')", encoding="utf-8")
        (self.dist / "assets" / "index-abc123.css").write_text("body{}", encoding="utf-8")
        (self.dist / "assets" / "inter-latin.woff2").write_bytes(b"wOF2")
        (self.dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
        (self.dist / "THIRD_PARTY_NOTICES.txt").write_text("THIRD-PARTY SOFTWARE NOTICES", encoding="utf-8")
        # Things that must never be served, placed where a careless file server might find them.
        (self.dist / "db.sqlite3").write_text("not served", encoding="utf-8")
        (self.dist / ".env").write_text("SECRET_KEY=never", encoding="utf-8")
        (self.dist / "assets" / "notes.txt").write_text("not a panel file type", encoding="utf-8")
        patcher = override_settings(FRONTEND_DIST=self.dist)
        patcher.enable()
        self.addCleanup(patcher.disable)


class PanelPageTests(BuiltPanelMixin, TestCase):
    def test_every_panel_address_gets_the_app(self):
        for path in ["/", "/setup", "/setup/", "/accounts", "/accounts/", "/activity/", "/settings", "/settings/"]:
            with self.subTest(path=path):
                res = self.client.get(path)
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res["X-Alertbot-Panel"], "react")
                self.assertContains(res, '<div id="root"></div>')

    def test_the_page_sets_the_csrf_cookie_and_strict_headers(self):
        res = self.client.get("/accounts")
        self.assertEqual(res.cookies["csrftoken"]["samesite"], "Strict")
        csp = res["Content-Security-Policy"]
        for rule in ["default-src 'self'", "script-src 'self'", "style-src 'self'", "connect-src 'self'",
                     "frame-ancestors 'none'", "object-src 'none'", "base-uri 'none'"]:
            self.assertIn(rule, csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertEqual(res["X-Frame-Options"], "DENY")
        self.assertEqual(res["Cache-Control"], "no-cache")

    def test_the_page_only_answers_reads(self):
        res = self.client.post("/settings", {}, content_type="application/json")
        self.assertIn(res.status_code, (403, 405))

    def test_unknown_addresses_are_not_the_app(self):
        self.assertEqual(self.client.get("/no-such-page").status_code, 404)
        res = self.client.get("/api/no-such-endpoint/")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["code"], "not_found")

    def test_the_api_is_unchanged(self):
        res = self.client.get("/api/settings/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/json")
        self.assertIn("default-src 'none'", res["Content-Security-Policy"])

    def test_a_new_build_is_picked_up_without_a_restart(self):
        self.assertNotContains(self.client.get("/"), "version-two")
        index = self.dist / "index.html"
        index.write_text(INDEX.replace("abc123", "version-two"), encoding="utf-8")
        stat = index.stat()
        import os
        os.utime(index, (stat.st_atime, stat.st_mtime + 5))
        self.assertContains(self.client.get("/"), "version-two")


class PanelFilesTests(BuiltPanelMixin, TestCase):
    def test_built_files_are_served_with_the_right_type_and_long_caching(self):
        for name, content_type in [("index-abc123.js", "text/javascript; charset=utf-8"),
                                   ("index-abc123.css", "text/css; charset=utf-8"),
                                   ("inter-latin.woff2", "font/woff2")]:
            with self.subTest(name=name):
                res = self.client.get(f"/assets/{name}")
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res["Content-Type"], content_type)
                self.assertEqual(res["Cache-Control"], "public, max-age=31536000, immutable")
                self.assertEqual(res["X-Content-Type-Options"], "nosniff")
                res.close()
        res = self.client.get("/favicon.svg")
        self.assertEqual(res["Content-Type"], "image/svg+xml")
        res.close()

    def test_the_third_party_notices_are_served_as_plain_text(self):
        res = self.client.get("/third-party-notices.txt")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(res["X-Content-Type-Options"], "nosniff")
        self.assertEqual(b"".join(res.streaming_content), b"THIRD-PARTY SOFTWARE NOTICES")
        (self.dist / "THIRD_PARTY_NOTICES.txt").unlink()
        self.assertEqual(self.client.get("/third-party-notices.txt").status_code, 404)

    def test_nothing_outside_the_assets_folder_can_be_reached(self):
        attempts = [
            "/assets/../index.html", "/assets/..%2Fdb.sqlite3", "/assets/%2e%2e%2f.env", "/assets/..\\db.sqlite3",
            "/assets/.env", "/assets/db.sqlite3", "/assets/notes.txt", "/assets/", "/assets/missing.js",
            "/db.sqlite3", "/.env", "/frontend/dist/index.html", "/static/db.sqlite3", "/backups/",
            "/assets/%00.js", "/assets/sub%2Findex-abc123.js",
        ]
        for path in attempts:
            with self.subTest(path=path):
                res = self.client.get(path)
                self.assertIn(res.status_code, (404, 400, 301), path)
                body = b"".join(res.streaming_content) if res.streaming else res.content
                res.close()
                self.assertNotIn(b"SECRET_KEY", body)
                self.assertNotIn(b"not served", body)

    def test_the_real_database_and_settings_are_never_served(self):
        db = Path(settings.DATABASES["default"]["NAME"]).name
        for path in [f"/assets/{db}", "/assets/.env", f"/{db}", "/.env", "/backups/", "/classic/../.env"]:
            with self.subTest(path=path):
                res = self.client.get(path)
                res.close()
                self.assertIn(res.status_code, (404, 400, 301))

    def test_files_only_answer_reads(self):
        res = self.client.head("/assets/index-abc123.js")
        res.close()
        self.assertEqual(res.status_code, 200)
        self.assertIn(self.client.post("/assets/index-abc123.js", {}, content_type="application/json").status_code, (403, 405))


class NotBuiltTests(TestCase):
    """Without a build (a source checkout without Node.js), a short page says how to build it."""

    def test_a_missing_build_says_how_to_build_it(self):
        with tempfile.TemporaryDirectory() as empty, override_settings(FRONTEND_DIST=Path(empty)):
            res = self.client.get("/")
            self.assertEqual(res.status_code, 503)
            self.assertEqual(res["X-Alertbot-Panel"], "not-built")
            self.assertContains(res, "npm run build", status_code=503)
            self.assertIn("default-src 'none'", res["Content-Security-Policy"])
            res = self.client.get("/assets/index-abc123.js")
            res.close()
            self.assertEqual(res.status_code, 404)
            self.assertEqual(self.client.get("/api/status/").status_code, 200, "the API works without the panel")

    def test_the_previous_page_is_gone(self):
        self.assertEqual(self.client.get("/classic/").status_code, 404)
        self.assertFalse((Path(settings.BASE_DIR) / "templates" / "index.html").exists())


class SourceTreeTests(TestCase):
    """The frontend source keeps to the rules the server relies on."""
    root = Path(settings.BASE_DIR) / "frontend"

    def test_the_build_settings_match_what_django_serves(self):
        config = (self.root / "vite.config.ts").read_text(encoding="utf-8")
        self.assertIn("thirdPartyNotices()", config, "the build ships THIRD_PARTY_NOTICES.txt")
        self.assertIn('assetsDir: "assets"', config)
        self.assertIn("assetsInlineLimit: 0", config, "no inline data: files; the CSP allows the site's own files")

    def test_the_panel_only_calls_its_own_api(self):
        for path in (self.root / "src").rglob("*.ts*"):
            if "test" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotRegex(text, r"fetch\(\s*[\"'`]https?://", "every request goes to this site")
                self.assertNotIn("dangerouslySetInnerHTML", text)
                self.assertNotIn("localStorage.setItem(\"alertbot-password", text)

    def test_dependencies_are_pinned_exactly(self):
        import json

        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        for section in ("dependencies", "devDependencies"):
            for name, version in package[section].items():
                with self.subTest(package=name):
                    self.assertRegex(version, r"^\d+\.\d+\.\d+$", "exact versions only (with package-lock.json)")
        self.assertTrue((self.root / "package-lock.json").is_file())
