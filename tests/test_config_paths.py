import os
import unittest
from unittest.mock import patch

import src.config as cfg


class TestConfigPaths(unittest.TestCase):
    def test_env_file_ignores_dead_pi_paths(self):
        # Only the Raspberry Pi candidates "exist" -> nothing is found.
        with patch("os.path.exists", lambda p: p.startswith("/home/pi/")):
            self.assertIsNone(cfg._find_env_file())

    def test_env_file_prefers_cwd_then_hermes(self):
        def exists(path):
            return path in {os.path.join(os.getcwd(), ".env"), os.path.expanduser("~/.hermes/.env")}

        with patch("os.path.exists", exists):
            self.assertEqual(cfg._find_env_file(), os.path.join(os.getcwd(), ".env"))

    def test_base_dir_without_gvw_home_is_cwd(self):
        env = {k: v for k, v in os.environ.items() if k != "GVW_HOME"}
        with patch.dict(os.environ, env, clear=True), patch("os.path.exists", lambda p: False):
            self.assertEqual(cfg._resolve_base_dir(), os.getcwd())

    def test_base_dir_honours_gvw_home(self):
        with patch.dict(os.environ, {"GVW_HOME": "/tmp/gvw"}):
            self.assertEqual(cfg._resolve_base_dir(), "/tmp/gvw")


if __name__ == "__main__":
    unittest.main()
