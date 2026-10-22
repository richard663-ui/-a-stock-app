import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy import verify_install as vi  # noqa: E402

COMMIT = "a" * 40


def link_dir(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.release = self.root / "releases" / COMMIT
        (self.release / "services").mkdir(parents=True)
        (self.release / "services" / "a.py").write_text("print('a')\n", encoding="utf-8")
        (self.release / "configs").mkdir()
        (self.release / "configs" / "c.json").write_text("{}", encoding="utf-8")
        (self.release / "services" / "__pycache__").mkdir()
        (self.release / "services" / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"x")

    def tearDown(self):
        if (self.root / "current").exists() and sys.platform == "win32":
            subprocess.run(["cmd", "/c", "rmdir", str(self.root / "current")], capture_output=True)
        self.tmp.cleanup()

    def install(self):
        manifest = vi.build_manifest(self.release, COMMIT, "test")
        vi.write_json_atomic(self.release / vi.MANIFEST_NAME, manifest)
        link_dir(self.root / "current", self.release)
        vi.write_json_atomic(self.root / vi.STATE_NAME, {"current_commit": COMMIT})
        return manifest

    def test_manifest_excludes_bytecode_and_itself(self):
        manifest = self.install()
        self.assertEqual(sorted(manifest["files"]), ["configs/c.json", "services/a.py"])

    def test_rejects_non_commit_identifiers(self):
        with self.assertRaises(ValueError):
            vi.build_manifest(self.release, "main", "test")

    def test_verify_ok_then_detects_modified_missing_and_extra_code(self):
        self.install()
        self.assertTrue(vi.verify(self.root)["ok"])
        (self.release / "services" / "a.py").write_text("print('tampered')\n", encoding="utf-8")
        report = vi.verify(self.root)
        self.assertFalse(report["ok"])
        self.assertIn("modified", " ".join(report["problems"]))
        (self.release / "services" / "a.py").write_text("print('a')\n", encoding="utf-8")
        (self.release / "services" / "dropped_in.py").write_text("x=1\n", encoding="utf-8")
        self.assertIn("unexpected", " ".join(vi.verify(self.root)["problems"]))
        (self.release / "services" / "dropped_in.py").unlink()
        (self.release / "configs" / "c.json").unlink()
        self.assertIn("missing", " ".join(vi.verify(self.root)["problems"]))

    def test_non_code_extra_files_are_tolerated(self):
        self.install()
        (self.release / "notes.txt").write_text("local note", encoding="utf-8")
        self.assertTrue(vi.verify(self.root)["ok"])

    def test_unpinned_state_fails_closed(self):
        self.assertFalse(vi.verify(self.root)["ok"])
        vi.write_json_atomic(self.root / vi.STATE_NAME, {"current_commit": "main"})
        self.assertFalse(vi.verify(self.root)["ok"])

    def test_cli_exit_codes(self):
        self.install()
        rc = vi.main(["verify", "--install-root", str(self.root), "--quiet"])
        self.assertEqual(rc, 0)
        status = json.loads((self.root / "runtime" / "integrity_status.json").read_text(encoding="utf-8"))
        self.assertTrue(status["ok"])
        (self.release / "services" / "a.py").write_text("tampered", encoding="utf-8")
        self.assertEqual(vi.main(["verify", "--install-root", str(self.root), "--quiet"]), 3)


class RegistryAndDownloadTests(unittest.TestCase):
    def test_service_registry_scripts_exist_and_names_unique(self):
        registry = json.loads((ROOT / "deploy" / "services.json").read_text(encoding="utf-8"))
        ids = [s["id"] for s in registry["services"]]
        self.assertEqual(len(ids), len(set(ids)))
        for key in ("watchdog", "startup", "log"):
            values = [s[key] for s in registry["services"]]
            self.assertEqual(len(values), len(set(values)), key)
        for svc in registry["services"]:
            self.assertTrue((ROOT / svc["script"]).is_file(), svc["script"])
        self.assertIn("collection_monitor", ids)

    def test_no_runtime_code_downloads_from_github(self):
        pattern = re.compile(r"raw\.githubusercontent\.com|codeload\.github\.com|api\.github\.com/repos/.+/contents")
        offenders = []
        for folder in ("modules", "services", "supabase", "docs"):
            for path in (ROOT / folder).rglob("*"):
                if path.suffix in {".py", ".ts", ".js", ".ps1", ".bat", ".cmd", ".html"} and path.is_file():
                    if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                        offenders.append(str(path.relative_to(ROOT)))
        for path in ROOT.glob("*.bat"):
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(path.name)
        self.assertEqual(offenders, [], "Code must ship in pinned releases, not be fetched at runtime")

    def test_installer_only_accepts_full_commit_ids(self):
        text = (ROOT / "deploy" / "install_runtime.ps1").read_text(encoding="utf-8")
        self.assertIn("'^[0-9a-f]{40}$'", text)
        self.assertTrue(text.isascii(), "PowerShell 5.1 scripts must stay ASCII")

    def test_autotrain_fails_closed_without_trainers(self):
        import services.l1_ml_autotrain_daemon_v2 as daemon
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(daemon.TrainerFilesMissing):
                daemon._ensure(Path(tmp) / "missing.py", "marker")
            path = Path(tmp) / "old.py"
            path.write_text("old version", encoding="utf-8")
            with self.assertRaises(daemon.TrainerFilesMissing):
                daemon._ensure(path, "new-marker")
        daemon._ensure_stack()   # the real release contains every trainer


if __name__ == "__main__":
    unittest.main()
