"""Self-update channel: version order, update.json validation, download verification."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mix_update  # noqa: E402
from mix_update import UpdateError  # noqa: E402


def installer_zip(version="1.0.1", folder="Awesome Mix", name="Awesome Mix", extra=None, drop=()):
    buffer = io.BytesIO()
    files = {
        "plugin.json": json.dumps({"name": name}),
        "package.json": json.dumps({"version": version}),
        "main.py": "# backend\n",
        "dist/index.js": "// bundle\n",
    }
    with zipfile.ZipFile(buffer, "w") as archive:
        for rel, text in files.items():
            if rel not in drop:
                archive.writestr(f"{folder}/{rel}", text)
        for entry in extra or ():
            archive.writestr(entry, "x")
    return buffer.getvalue()


def manifest_for(data: bytes, version="1.0.1", **overrides):
    payload = {"version": version, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
               "notes": "Notes", "url": mix_update.ARTIFACT_URL}
    payload.update(overrides)
    return payload


class FakeTransport:
    def __init__(self, manifest, artifact=b"", fail=None):
        self.manifest = manifest
        self.artifact = artifact
        self.fail = fail
        self.reads = 0
        self.downloads = 0

    def read(self, url, max_bytes):
        assert url.startswith(mix_update.MANIFEST_URL + "?t="), url
        self.reads += 1
        if self.fail:
            raise UpdateError(mix_update.NETWORK, self.fail)
        return self.manifest if isinstance(self.manifest, bytes) else json.dumps(self.manifest).encode()

    def download(self, url, dest, max_bytes):
        assert url.startswith(mix_update.ARTIFACT_URL + "?t="), url
        self.downloads += 1
        with open(dest, "wb") as handle:
            handle.write(self.artifact[: max_bytes + 1])


class VersionTests(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(mix_update.parse_version("1.0.0"), (1, 0, 0))
        self.assertEqual(mix_update.parse_version(" 2.10.3 "), (2, 10, 3))
        for bad in ("1.0", "1.0.0-rc.1", "v1.0.0", "1.0.0.0", "", None, 100, "a.b.c"):
            self.assertIsNone(mix_update.parse_version(bad), bad)

    def test_is_newer_compares_numbers(self):
        self.assertTrue(mix_update.is_newer("1.0.0", "1.0.1"))
        self.assertTrue(mix_update.is_newer("1.0.9", "1.0.10"))
        self.assertTrue(mix_update.is_newer("1.9.9", "2.0.0"))
        self.assertFalse(mix_update.is_newer("1.0.1", "1.0.1"))
        self.assertFalse(mix_update.is_newer("1.0.1", "1.0.0"))
        self.assertFalse(mix_update.is_newer("1.0.0", "1.0.1-rc.1"))
        self.assertFalse(mix_update.is_newer("garbage", "9.9.9"))


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.data = installer_zip()

    def test_valid_manifest(self):
        parsed = mix_update.parse_manifest(manifest_for(self.data, notes="  hi  "))
        self.assertEqual(parsed["version"], "1.0.1")
        self.assertEqual(parsed["size"], len(self.data))
        self.assertEqual(parsed["notes"], "hi")

    def test_channel_is_fixed(self):
        self.assertEqual(mix_update.MANIFEST_URL, "https://raw.githubusercontent.com/M2ljay/Eternal-Mix/release/update.json")
        self.assertEqual(mix_update.ARTIFACT_URL, "https://raw.githubusercontent.com/M2ljay/Eternal-Mix/release/awesome-mix.zip")

    def test_invalid_manifests_are_refused(self):
        good = manifest_for(self.data)
        cases = {
            "not an object": [good],
            "bad version": dict(good, version="1.0.1-beta"),
            "missing version": {k: v for k, v in good.items() if k != "version"},
            "upper-case sha": dict(good, sha256=good["sha256"].upper()),
            "short sha": dict(good, sha256="abc"),
            "zero size": dict(good, size=0),
            "bool size": dict(good, size=True),
            "string size": dict(good, size="12"),
            "huge size": dict(good, size=mix_update.MAX_ARTIFACT_BYTES + 1),
            "other url": dict(good, url="https://example.com/awesome-mix.zip"),
            "other repo": dict(good, url=mix_update.ARTIFACT_URL.replace("M2ljay", "someone")),
            "no url": {k: v for k, v in good.items() if k != "url"},
        }
        for label, payload in cases.items():
            with self.subTest(label), self.assertRaises(UpdateError) as caught:
                mix_update.parse_manifest(payload)
            self.assertEqual(caught.exception.reason, mix_update.MANIFEST_INVALID)

    def test_non_json_is_refused(self):
        for body in (b"<html>", b"\xff\xfe", b""):
            with self.assertRaises(UpdateError) as caught:
                mix_update.decode_manifest(body)
            self.assertEqual(caught.exception.reason, mix_update.MANIFEST_INVALID)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.data = installer_zip()
        self.manifest = mix_update.parse_manifest(manifest_for(self.data))
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def write(self, data):
        path = os.path.join(self.directory.name, "a.zip")
        Path(path).write_bytes(data)
        return path

    def test_matching_download_passes(self):
        mix_update.verify_download(len(self.data), hashlib.sha256(self.data).hexdigest(), self.manifest)
        mix_update.verify_installer_layout(self.write(self.data), "1.0.1")

    def test_size_mismatch(self):
        with self.assertRaises(UpdateError) as caught:
            mix_update.verify_download(len(self.data) + 1, self.manifest["sha256"], self.manifest)
        self.assertEqual(caught.exception.reason, mix_update.SIZE_MISMATCH)

    def test_hash_mismatch(self):
        with self.assertRaises(UpdateError) as caught:
            mix_update.verify_download(len(self.data), "0" * 64, self.manifest)
        self.assertEqual(caught.exception.reason, mix_update.HASH_MISMATCH)

    def test_layout_keeps_the_awesome_mix_folder(self):
        cases = {
            "other top folder": installer_zip(folder="Now Playing"),
            "other plugin name": installer_zip(name="Now Playing"),
            "other version": installer_zip(version="1.0.2"),
            "second top folder": installer_zip(extra=["Other/plugin.json"]),
            "traversal": installer_zip(extra=["Awesome Mix/../evil.py"]),
            "missing main.py": installer_zip(drop=("main.py",)),
            "missing bundle": installer_zip(drop=("dist/index.js",)),
            "not a zip": b"not a zip",
        }
        for label, data in cases.items():
            with self.subTest(label), self.assertRaises(UpdateError) as caught:
                mix_update.verify_installer_layout(self.write(data), "1.0.1")
            self.assertEqual(caught.exception.reason, mix_update.LAYOUT_INVALID)

    def test_layout_folder_is_plugin_json_name(self):
        plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(mix_update.PLUGIN_FOLDER, plugin["name"])

    def test_file_artifact(self):
        self.assertEqual(mix_update.file_artifact(r"C:\x\a.zip"), r"file://C:\x\a.zip")


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.staging = os.path.join(self.directory.name, "plugin-update")
        self.now = 1_000_000.0

    def channel(self, transport, current="1.0.0"):
        return mix_update.UpdateChannel(current, self.staging, transport, clock=lambda: self.now)

    def downloads(self):
        folder = os.path.join(self.staging, "download")
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def test_check_reports_a_newer_version(self):
        data = installer_zip()
        result = self.channel(FakeTransport(manifest_for(data))).check()
        self.assertTrue(result["ok"])
        self.assertTrue(result["updateAvailable"])
        self.assertEqual((result["current"], result["version"], result["size"]), ("1.0.0", "1.0.1", len(data)))

    def test_check_same_version_is_not_an_update(self):
        result = self.channel(FakeTransport(manifest_for(installer_zip(), version="1.0.0"))).check()
        self.assertTrue(result["ok"])
        self.assertFalse(result["updateAvailable"])

    def test_check_is_throttled_and_remembered(self):
        transport = FakeTransport(manifest_for(installer_zip()))
        channel = self.channel(transport)
        channel.check()
        channel.check()
        self.assertEqual(transport.reads, 1)
        channel.check(force=True)
        self.assertEqual(transport.reads, 2)
        restarted_transport = FakeTransport(manifest_for(installer_zip()))
        restarted = self.channel(restarted_transport)
        self.assertTrue(restarted.check()["updateAvailable"])
        self.assertEqual(restarted_transport.reads, 0)
        self.now += mix_update.CHECK_INTERVAL_SECONDS + 1
        restarted.check()
        self.assertEqual(restarted_transport.reads, 1)

    def test_network_and_manifest_failures(self):
        result = self.channel(FakeTransport({}, fail="offline")).check()
        self.assertEqual((result["ok"], result["reason"]), (False, mix_update.NETWORK))
        result = self.channel(FakeTransport(b"<html>")).check(force=True)
        self.assertEqual((result["ok"], result["reason"]), (False, mix_update.MANIFEST_INVALID))

    def test_prepare_hands_on_a_verified_file(self):
        data = installer_zip()
        result = self.channel(FakeTransport(manifest_for(data), data)).prepare()
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["artifact"].startswith("file://"))
        path = result["artifact"][len("file://"):]
        self.assertEqual(Path(path).read_bytes(), data)
        self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.downloads(), ["Eternal-Mix-decky_Installer-1.0.1.zip"])

    def test_prepare_refusals_leave_nothing_behind(self):
        data = installer_zip()
        cases = {
            mix_update.HASH_MISMATCH: (manifest_for(data, sha256="0" * 64), data),
            mix_update.SIZE_MISMATCH: (manifest_for(data), data + b"extra"),
            "short download": (manifest_for(data), data[:-10]),
            mix_update.LAYOUT_INVALID: (manifest_for(installer_zip(folder="Now Playing")), installer_zip(folder="Now Playing")),
            mix_update.UP_TO_DATE: (manifest_for(data, version="1.0.0"), data),
        }
        for label, (manifest, artifact) in cases.items():
            with self.subTest(label):
                transport = FakeTransport(manifest, artifact)
                result = self.channel(transport).prepare()
                self.assertFalse(result["ok"])
                expected = mix_update.SIZE_MISMATCH if label == "short download" else label
                self.assertEqual(result["reason"], expected)
                self.assertTrue(result["message"])
                self.assertEqual(self.downloads(), [])
                if label == mix_update.UP_TO_DATE:
                    self.assertEqual(transport.downloads, 0)

    def test_prepare_rereads_the_manifest(self):
        old = installer_zip()
        transport = FakeTransport(manifest_for(old), old)
        channel = self.channel(transport)
        channel.check()
        newer = installer_zip(version="1.0.2")
        transport.manifest, transport.artifact = manifest_for(newer, version="1.0.2"), newer
        result = channel.prepare()
        self.assertEqual((result["ok"], result["version"]), (True, "1.0.2"))
        self.assertEqual(self.downloads(), ["Eternal-Mix-decky_Installer-1.0.2.zip"])

    def test_busy_while_running(self):
        channel = self.channel(FakeTransport(manifest_for(installer_zip())))
        channel._lock.acquire()
        try:
            self.assertEqual(channel.check()["reason"], mix_update.BUSY)
            self.assertEqual(channel.prepare()["reason"], mix_update.BUSY)
        finally:
            channel._lock.release()


class ReleaseFilesTests(unittest.TestCase):
    def test_update_module_ships(self):
        package_release = (ROOT / "scripts" / "package-release.py").read_text(encoding="utf-8")
        self.assertIn("'mix_update.py'", package_release)
        package_win = (ROOT / "package-win.ps1").read_text(encoding="utf-8")
        self.assertIn('Copy-Item (Join-Path $Root "mix_update.py") $Stage', package_win)


if __name__ == "__main__":
    unittest.main()
