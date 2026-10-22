import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import check_repo_hygiene as hygiene  # noqa: E402


class HygieneTests(unittest.TestCase):
    def test_patterns_catch_the_kinds_of_secrets_this_repo_once_held(self):
        samples = {
            # Built by concatenation so this test file itself does not trip the scanner.
            "committed password hash": "const PASSWORD_" + "HASH_B64 = '" + "QUJD" * 8 + "';",
            "supabase secret key": "key = 'sb_secret_" + "x" * 30 + "'",
            "JWT": "eyJ" + "a" * 20 + ".eyJ" + "b" * 20 + "." + "c" * 20,
        }
        for label, text in samples.items():
            self.assertTrue(hygiene.SECRET_PATTERNS[label].search(text), label)
        self.assertIsNone(hygiene.SECRET_PATTERNS["supabase secret key"].search("sb_secret_abc"))

    def test_current_tree_is_clean(self):
        self.assertEqual(hygiene.main(), 0)

    def test_every_module_is_in_the_component_registry(self):
        from tools import component_registry
        self.assertEqual(component_registry.main(["--check"]), 0)


if __name__ == "__main__":
    unittest.main()
