"""The whole check against a fake GitHub: a real local HTTP server answers like GitHub's API and
its release downloads (with GitHub's redirect). No real network: the client only allows GitHub's
own HTTPS hosts, plus this test server's 127.0.0.1 address when it is named explicitly.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from release_meta import MANIFEST_ASSET, REPOSITORY, github, manifest
from release_meta.tests import H, make_manifest

ZIP = "BlueskyDiscordAlertBot-{v}.zip"


class FakeGitHub:
    """Routes: path -> (status, body bytes, headers). Records every request path."""

    def __init__(self):
        self.routes, self.requests = {}, []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake.requests.append(self.path)
                status, body, headers = fake.routes.get(self.path, (404, b'{"message": "Not Found"}', {}))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def json(self, path, data, status=200, headers=None):
        self.routes[path] = (status, json.dumps(data).encode(), headers or {})

    def raw(self, path, body, status=200, headers=None):
        self.routes[path] = (status, body, headers or {})


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()
        self.addCleanup(self.gh.close)
        self.client = github.Client(api=self.gh.base, test_hosts=(self.gh.base + "/",))
        self.releases = []
        self.withdrawn({"schema": "alertbot-withdrawn/1", "revision": 2, "updated_at": "2026-12-01", "withdrawn": []})

    def withdrawn(self, data, status=200):
        self.gh.json(f"/repos/{REPOSITORY}/contents/release/withdrawn.json?ref=main", data, status)

    def publish(self, version, *, data=None, immutable=True, draft=False, prerelease=None, zip_size=4096,
                zip_digest=f"sha256:{H}", manifest_path=None):
        """A release on the fake GitHub. Its manifest download goes through a redirect, like GitHub's."""
        data = data if data is not None else make_manifest(version)
        manifest_path = manifest_path or f"/download/v{version}/{MANIFEST_ASSET}"
        location = manifest_path if manifest_path.startswith("http") else self.gh.base + manifest_path
        self.gh.raw(f"/dl/{version}/manifest", b"", 302, {"Location": location})
        if manifest_path.startswith("/"):
            self.gh.json(manifest_path, data)
        self.releases.append({
            "tag_name": f"v{version}", "draft": draft, "immutable": immutable,
            "prerelease": ("-" in version) if prerelease is None else prerelease,
            "assets": [{"name": MANIFEST_ASSET, "size": 900, "digest": None,
                        "browser_download_url": f"{self.gh.base}/dl/{version}/manifest"},
                       {"name": ZIP.format(v=version), "size": zip_size, "digest": zip_digest,
                        "browser_download_url": f"{self.gh.base}/dl/{version}/zip"}]})
        self.gh.json(f"/repos/{REPOSITORY}/releases?per_page=100", self.releases)

    def check(self, installed="1.0.0-rc.2", channel="prerelease", **kw):
        return github.check(installed, channel, client=self.client, **kw)

    # -- the normal cases ------------------------------------------------------------------------
    def test_an_update_is_found_and_pulled_by_digest(self):
        self.publish("1.0.0-rc.2")
        self.publish("1.0.0")
        d = self.check()
        self.assertEqual((d.action, str(d.target.version)), ("update", "1.0.0"))
        self.assertTrue(d.target.image_reference.endswith("@sha256:" + "1" * 64))
        self.assertNotIn("/download/v1.0.0-rc.2/" + MANIFEST_ASSET, self.gh.requests,
                         "manifests of versions that can't be chosen aren't downloaded")

    def test_up_to_date(self):
        self.publish("1.0.0")
        d = self.check("1.0.0", "stable")
        self.assertEqual(d.action, "up_to_date")

    def test_drafts_are_ignored(self):
        self.publish("1.0.0")
        self.publish("1.1.0", draft=True)
        d = self.check("1.0.0", "stable")
        self.assertEqual(d.action, "up_to_date")
        self.assertNotIn("/dl/1.1.0/manifest", self.gh.requests, "a draft's manifest isn't even read")

    def test_releases_on_a_second_page_are_found(self):
        self.publish("1.0.0")
        page2 = [dict(self.releases[0], tag_name="v1.1.0", assets=[
            {"name": MANIFEST_ASSET, "size": 900, "digest": None, "browser_download_url": f"{self.gh.base}/dl/1.1.0/m"},
            {"name": ZIP.format(v="1.1.0"), "size": 4096, "digest": f"sha256:{H}", "browser_download_url": "x"}])]
        self.gh.json("/dl/1.1.0/m", make_manifest("1.1.0"))
        self.gh.json(f"/repos/{REPOSITORY}/releases?per_page=100", self.releases,
                     headers={"Link": f'<{self.gh.base}/page2>; rel="next"'})
        self.gh.json("/page2", page2)
        self.assertEqual(str(self.check("1.0.0", "stable").target.version), "1.1.0")

    # -- metadata that can't be read: nothing is installed -----------------------------------------
    def assertBlocked(self, decision, reason, text=""):
        self.assertEqual((decision.action, decision.reason), ("blocked", reason), decision.message)
        self.assertIn(text, decision.message)
        self.assertIn("Nothing was changed.", decision.message)

    def test_withdrawn_list_missing(self):
        self.publish("1.0.0")
        self.withdrawn({"message": "Not Found"}, status=404)
        self.assertBlocked(self.check(), "metadata_unavailable", "GitHub answered 404")

    def test_withdrawn_list_broken(self):
        self.publish("1.0.0")
        self.gh.raw(f"/repos/{REPOSITORY}/contents/release/withdrawn.json?ref=main", b"{not json")
        self.assertBlocked(self.check(), "metadata_unavailable", "isn't valid JSON")
        self.withdrawn({"schema": "alertbot-withdrawn/1", "revision": 0, "updated_at": "2026-12-01", "withdrawn": []})
        self.assertBlocked(self.check(), "metadata_unavailable", "revision")

    def test_withdrawn_list_older_than_one_seen(self):
        self.publish("1.0.0")
        self.assertBlocked(self.check(seen_revision=5), "withdrawn_stale", "revision 2")

    def test_rate_limited(self):
        self.gh.json(f"/repos/{REPOSITORY}/releases?per_page=100", {"message": "API rate limit exceeded"}, 403)
        self.assertBlocked(self.check(), "metadata_unavailable", "limiting requests")

    def test_github_unreachable(self):
        self.publish("1.0.0")
        self.gh.close()
        self.assertBlocked(self.check(), "metadata_unavailable", "couldn't be reached")

    def test_a_release_list_that_isnt_a_list(self):
        self.gh.json(f"/repos/{REPOSITORY}/releases?per_page=100", {"oops": True})
        self.assertBlocked(self.check(), "metadata_unavailable", "malformed")

    # -- releases that can't be trusted: nothing is installed, no fallback --------------------------
    def test_a_release_that_isnt_immutable(self):
        self.publish("1.0.0", immutable=False)
        self.assertBlocked(self.check(), "invalid_release", "isn't an immutable release")

    def test_a_manifest_for_another_version(self):
        self.publish("1.0.0", data=make_manifest("1.0.1"))
        self.assertBlocked(self.check(), "invalid_release", "attached to v1.0.0")

    def test_a_tampered_download(self):
        self.publish("1.0.0", zip_digest="sha256:" + "b" * 64)
        self.assertBlocked(self.check(), "invalid_release", "different hash")
        self.releases.clear()
        self.publish("1.0.0", zip_size=1)
        self.assertBlocked(self.check(), "invalid_release", "different size")

    def test_a_wrong_prerelease_flag(self):
        self.publish("1.0.0", prerelease=True)
        self.assertBlocked(self.check(), "invalid_release", "pre-release flag")

    def test_a_manifest_naming_another_image(self):
        self.publish("1.0.0", data=make_manifest("1.0.0", **{"image.name": "ghcr.io/someone/else"}))
        self.assertBlocked(self.check(), "invalid_release", "another image")

    def test_a_newest_bad_release_doesnt_fall_back_to_an_older_one(self):
        self.publish("1.0.0")
        self.publish("1.1.0", immutable=False)
        self.assertBlocked(self.check("1.0.0-rc.2"), "invalid_release", "1.1.0")

    def test_withdrawing_the_bad_release_unblocks(self):
        self.publish("1.0.0")
        self.publish("1.1.0", immutable=False)
        self.withdrawn({"schema": "alertbot-withdrawn/1", "revision": 3, "updated_at": "2026-12-02", "withdrawn": [
            {"version": "1.1.0", "withdrawn_at": "2026-12-02", "reason": "Published by mistake.", "replacement": None}]})
        self.assertEqual(str(self.check("1.0.0-rc.2", seen_revision=2).target.version), "1.0.0")

    def test_a_redirect_to_another_site_is_refused(self):
        self.publish("1.0.0", manifest_path="http://evil.example/manifest.json")
        self.assertBlocked(self.check(), "invalid_release", "unexpected address")

    def test_an_oversized_manifest_is_refused(self):
        self.publish("1.0.0")
        self.gh.raw(f"/download/v1.0.0/{MANIFEST_ASSET}", b" " * (manifest.MAX_BYTES + 10) + b"{}")
        self.assertBlocked(self.check(), "invalid_release", "larger than allowed")


class ClientSafetyTests(unittest.TestCase):
    def test_only_github_or_a_named_local_test_server(self):
        github.Client()  # the real API
        for api, hosts in (("https://evil.example", ()), ("http://api.github.com", ()),
                           ("http://127.0.0.1:9", ())):
            with self.subTest(api=api), self.assertRaises(ValueError):
                github.Client(api=api, test_hosts=hosts)
        with self.assertRaises(ValueError):
            github.Client(api="http://192.168.1.5:80", test_hosts=("http://192.168.1.5:80/",))

    def test_allowed_hosts_are_https_github_only(self):
        for url in ("https://api.github.com/x", "https://github.com/x/releases/download/a",
                    "https://objects.githubusercontent.com/x", "https://release-assets.githubusercontent.com/x"):
            self.assertTrue(github._allowed(url, ()), url)
        for url in ("http://github.com/x", "https://github.com.evil.example/x", "https://evil.example/github.com",
                    "https://raw.githubusercontent.com/x", "ftp://github.com/x"):
            self.assertFalse(github._allowed(url, ()), url)


if __name__ == "__main__":
    unittest.main()
