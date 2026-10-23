"""Release metadata from a real multi-platform build, pushed to a local registry. Opt-in, slow
(a linux/amd64 + linux/arm64 build, ARM under emulation: about 5-15 minutes):

    set RUN_DOCKER_TESTS=1
    venv\\Scripts\\python manage.py test config.tests_docker_release

What the release workflow will do (docs/RELEASES.md, section 5), step by step, on this computer:
1. build both platforms and push them to a local registry (registry:2), on a private Docker
   network shared only with a temporary builder (published to this computer on 127.0.0.1 only);
2. read the image index raw from the registry: its SHA-256 is the digest the build reported;
3. run `describe` in that image. Docker Desktop can't pull from a registry on 127.0.0.1 (its
   engine runs in a VM), so the amd64 image is loaded from the same build instead, and proven to
   be the pushed one: its image ID is the config digest in the amd64 manifest the index lists;
4. copy the deployment files out of the image with `docker cp` (what the Windows updater will
   do: no Git, Python or Node.js needed on the computer);
5. build the download from those files, then the manifest and SHA256SUMS (release_meta).
Then it changes things to show they are caught: a tag moved to another image, a file of the
download changed, a deployment file changed inside a container.

Isolation: a temporary buildx builder and registry, image and container names starting with
"alertbot-test-", nothing pushed anywhere else, no network for the containers it runs. Cleanup
removes all of them. Nothing is published.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import uuid
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
VERSION = "1.0.0-rc.99"
REGISTRY_IMAGE = "registry:2.8.3"
INDEX_TYPES = "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json"


def docker_available():
    if os.environ.get("RUN_DOCKER_TESTS") != "1" or shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0


def run(*args, ok=True, timeout=600, **kw):
    res = subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=timeout, **kw)
    if ok and res.returncode != 0:
        raise AssertionError(f"{' '.join(args[:4])}... failed:\n{res.stdout[-2000:]}\n{res.stderr[-3000:]}")
    return res


@unittest.skipUnless(docker_available(), "set RUN_DOCKER_TESTS=1 with Docker running")
class ReleaseFromARealBuildTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sys.path.insert(0, str(ROOT / "docker"))
        cls.tmp = Path(tempfile.mkdtemp())
        cls.name = f"alertbot-test-{uuid.uuid4().hex[:8]}"
        cls.registry = f"{cls.name}-registry"
        cls.builder = f"{cls.name}-builder"
        cls.cleanups = []
        try:
            cls._start()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _start(cls):
        cls.network = f"{cls.name}-net"
        run("docker", "network", "create", cls.network)
        cls.cleanups.append(["docker", "network", "rm", cls.network])
        run("docker", "run", "-d", "--name", cls.registry, "--network", cls.network, "-p", "127.0.0.1::5000",
            REGISTRY_IMAGE)
        cls.cleanups.append(["docker", "rm", "-f", cls.registry])
        port = run("docker", "port", cls.registry, "5000/tcp").stdout.strip().rsplit(":", 1)[1]
        cls.host = f"127.0.0.1:{port}"            # this computer -> the registry
        cls.repo = f"{cls.registry}:5000/{cls.name}"  # the builder -> the registry, on the private network
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://{cls.host}/v2/", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        config = cls.tmp / "buildkitd.toml"
        config.write_text(f'[registry."{cls.registry}:5000"]\n  http = true\n', encoding="utf-8")
        run("docker", "buildx", "create", "--name", cls.builder, "--driver", "docker-container",
            "--driver-opt", f"network={cls.network}", "--buildkitd-config", str(config))
        cls.cleanups.append(["docker", "buildx", "rm", "--force", cls.builder])
        metadata = cls.tmp / "build.json"
        build = ["docker", "buildx", "build", "--builder", cls.builder, "--build-arg", f"ALERTBOT_VERSION={VERSION}"]
        run(*build, "--platform", "linux/amd64,linux/arm64", "--push", "--metadata-file", str(metadata),
            "-t", f"{cls.repo}:{VERSION}", str(ROOT), timeout=3600)
        cls.digest = json.loads(metadata.read_text(encoding="utf-8"))["containerimage.digest"]

        # 2. the index, raw, from the registry
        cls.index_raw = cls.registry_get(f"/v2/{cls.name}/manifests/{cls.digest}")
        # 3. the amd64 image, loaded from the same build, proven to be the pushed one
        cls.local = f"{cls.name}:amd64"
        run(*build, "--platform", "linux/amd64", "--load", "-t", cls.local, str(ROOT), timeout=3600)
        cls.cleanups.append(["docker", "image", "rm", "-f", cls.local])
        amd64 = next(m for m in json.loads(cls.index_raw)["manifests"]
                     if m.get("platform", {}).get("architecture") == "amd64")
        manifest_raw = cls.registry_get(f"/v2/{cls.name}/manifests/{amd64['digest']}",
                                        accept="application/vnd.oci.image.manifest.v1+json")
        assert "sha256:" + hashlib.sha256(manifest_raw).hexdigest() == amd64["digest"]
        cls.config_digest = json.loads(manifest_raw)["config"]["digest"]
        cls.local_id = run("docker", "image", "inspect", "-f", "{{.Id}}", cls.local).stdout.strip()
        cls.pinned = cls.local
        cls.describe = json.loads(run("docker", "run", "--rm", "--network", "none", cls.pinned, "describe").stdout)
        # 4. the deployment files, with docker cp only
        container = f"{cls.name}-extract"
        run("docker", "create", "--name", container, cls.pinned)
        try:
            cls.extracted = cls.tmp / "extracted"
            cls.extracted.mkdir()
            run("docker", "cp", f"{container}:/app/bundle/windows", str(cls.extracted))
        finally:
            run("docker", "rm", "-f", container, ok=False)
        # 5. the download, the manifest and SHA256SUMS
        import make_release_bundle as mrb
        from release_meta import IMAGE, generate

        cls.out = cls.tmp / "dist"
        cls.zip = mrb.make(f"{IMAGE}:{VERSION}@{cls.digest}", cls.out, from_bundle=cls.extracted / "windows")
        cls.compat = json.loads((ROOT / "release" / "compatibility.json").read_text(encoding="utf-8"))
        cls.manifest = generate.generate(version=VERSION, commit="c" * 40, run_id="1", index_digest=cls.digest,
                                         index_raw=cls.index_raw, describe=cls.describe, bundle_path=cls.zip,
                                         compatibility=cls.compat)
        generate.write_release_files(cls.out, cls.manifest, cls.zip)

    @classmethod
    def registry_get(cls, path, accept=INDEX_TYPES):
        request = urllib.request.Request(f"http://{cls.host}{path}", headers={"Accept": accept})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()

    @classmethod
    def tearDownClass(cls):
        for command in reversed(cls.cleanups):
            subprocess.run(command, capture_output=True, timeout=300)
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    # -- what was built ----------------------------------------------------------------------------
    def test_the_digest_is_the_index_and_it_lists_both_platforms(self):
        from release_meta import IMAGE

        self.assertEqual("sha256:" + hashlib.sha256(self.index_raw).hexdigest(), self.digest)
        self.assertEqual(set(self.manifest["image"]["platforms"]), {"linux/amd64", "linux/arm64"})
        self.assertEqual(self.manifest["image"]["digest"], self.digest)
        self.assertEqual(self.manifest["image"]["name"], IMAGE, "the registry differs, the digest doesn't")

    def test_the_image_run_here_is_the_pushed_one(self):
        self.assertEqual(self.local_id, self.config_digest,
                         "index digest -> amd64 manifest (hash checked) -> config digest == the local image ID")

    def test_the_image_describes_itself(self):
        self.assertEqual((self.describe["version"], self.describe["version_is_release"]), (VERSION, True))
        self.assertEqual(self.describe["problems"], [])
        self.assertEqual(self.describe["platform"], "linux/amd64")
        self.assertEqual(self.manifest["compatibility"]["data_schema"], self.describe["data_schema"])

    def test_the_files_copied_out_are_this_checkouts_files(self):
        import make_release_bundle as mrb
        from release_meta import bundle

        files = bundle.verify(self.extracted / "windows")
        self.assertEqual(files, mrb.index(mrb.bundle_files())["files"], "the same bytes on Windows and in the image")
        self.assertEqual(files, self.describe["bundles"]["windows"]["files"])

    def test_the_manifest_is_valid_and_reproducible(self):
        from release_meta import MANIFEST_ASSET, generate, manifest

        manifest.validate(json.loads((self.out / MANIFEST_ASSET).read_text(encoding="utf-8")))
        self.assertEqual(sorted(generate.verify_checksums(self.out)), sorted([MANIFEST_ASSET, self.zip.name]))
        import make_release_bundle as mrb
        from release_meta import IMAGE

        again = mrb.make(f"{IMAGE}:{VERSION}@{self.digest}", self.tmp / "again", from_bundle=self.extracted / "windows")
        self.assertEqual(again.read_bytes(), self.zip.read_bytes())

    # -- what must be caught -----------------------------------------------------------------------
    def test_a_moved_tag_doesnt_change_what_the_digest_names(self):
        from release_meta import MetadataError, generate

        other = f"{self.name}-other"
        run("docker", "buildx", "build", "--builder", self.builder, "--platform", "linux/amd64", "--push",
            "-t", f"{self.repo}:{VERSION}", "-", input=f"FROM {REGISTRY_IMAGE}\nLABEL moved={other}\n", timeout=900)
        moved_raw = self.registry_get(f"/v2/{self.name}/manifests/{VERSION}", accept=INDEX_TYPES +
                                      ", application/vnd.oci.image.manifest.v1+json")
        self.assertNotEqual(hashlib.sha256(moved_raw).hexdigest(), self.digest.split(":")[1], "the tag now points elsewhere")
        with self.assertRaises(MetadataError):
            generate.platforms_from_index(moved_raw, self.digest)
        self.assertEqual(self.registry_get(f"/v2/{self.name}/manifests/{self.digest}"), self.index_raw,
                         "the digest still names exactly what was built")

    def test_a_changed_download_file_is_caught(self):
        from release_meta import MetadataError, generate

        copy = self.tmp / "tampered"
        shutil.copytree(self.out, copy)
        (copy / self.zip.name).write_bytes(self.zip.read_bytes() + b"x")
        with self.assertRaises(MetadataError):
            generate.verify_checksums(copy)

    def test_a_changed_deployment_file_inside_a_container_is_caught(self):
        res = run("docker", "run", "--rm", "--network", "none", "--entrypoint", "sh", self.pinned, "-c",
                  "echo x >> /app/bundle/windows/update.bat", ok=False)
        self.assertNotEqual(res.returncode, 0, "the bot's user can't change the deployment files")
        res = run("docker", "run", "--rm", "--network", "none", "--user", "0", "--entrypoint", "sh", self.pinned, "-c",
                  "echo x >> /app/bundle/windows/update.bat && /app/docker/entrypoint.sh describe", ok=False)
        self.assertEqual(res.returncode, 1)
        self.assertIn("update.bat doesn't match its hash", res.stdout)
