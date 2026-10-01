import json
import tempfile
import unittest
from pathlib import Path

from scripts.public_links import write_link_state


class PublicLinkLifecycleTests(unittest.TestCase):
    def test_start_stop_clears_stale_urls_and_ready_publishes_urls(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            target = folder / "links.json"
            target.write_text(json.dumps({"teacher": "https://old.trycloudflare.com/teacher/login"}))

            write_link_state(folder, status="starting")
            starting = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(starting["status"], "starting")
            self.assertIsNone(starting["teacher"])
            self.assertIsNone(starting["student"])

            write_link_state(folder, status="ready", origin="https://new.example.com")
            ready = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(ready["status"], "ready")
            self.assertEqual(ready["teacher"], "https://new.example.com/teacher/login")
            self.assertEqual(ready["student"], "https://new.example.com/student")

            write_link_state(folder, status="stopped")
            stopped = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(stopped["status"], "stopped")
            self.assertIsNone(stopped["teacher"])
            self.assertIsNone(stopped["student"])

    def test_ready_requires_public_origin(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                write_link_state(Path(root), status="ready")


if __name__ == "__main__":
    unittest.main()
