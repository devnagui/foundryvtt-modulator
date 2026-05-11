import unittest
from unittest.mock import patch

from backend.app.services.core import (
    _count_posix_process_name_occurrences,
    _count_windows_process_name_occurrences,
    _foundry_process_probe,
)


class TestFoundryOnlineDetection(unittest.TestCase):
    def test_count_windows_process_name_occurrences(self) -> None:
        csv_text = (
            "\"Foundry Virtual Tabletop.exe\",\"1234\",\"Console\",\"1\",\"120,000 K\"\n"
            "\"explorer.exe\",\"2000\",\"Console\",\"1\",\"50,000 K\"\n"
            "\"Foundry Virtual Tabletop.exe\",\"9999\",\"Console\",\"1\",\"121,000 K\"\n"
        )
        count = _count_windows_process_name_occurrences(csv_text, "Foundry Virtual Tabletop.exe")
        self.assertEqual(count, 2)

    @patch("backend.app.services.core.os.name", "nt")
    @patch("backend.app.services.core.subprocess.run")
    def test_foundry_process_probe_online(self, run_mock) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "\"Foundry Virtual Tabletop.exe\",\"1234\",\"Console\",\"1\",\"120,000 K\"\n"
        run_mock.return_value.stderr = ""
        payload = _foundry_process_probe("Foundry Virtual Tabletop.exe", "D:/foundry")
        self.assertTrue(payload["online"])
        self.assertEqual(payload["count"], 1)

    def test_count_posix_process_name_occurrences(self) -> None:
        ps_output = "node\npython3\nFoundryVTT\nnode\n"
        count = _count_posix_process_name_occurrences(ps_output, "node")
        self.assertEqual(count, 2)

    @patch("backend.app.services.core.os.name", "posix")
    @patch("backend.app.services.core.subprocess.run")
    def test_foundry_process_probe_posix_online(self, run_mock) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "node\nFoundryVTT\npython3\n"
        run_mock.return_value.stderr = ""
        payload = _foundry_process_probe("FoundryVTT", "/foundry")
        self.assertTrue(payload["online"])
        self.assertEqual(payload["count"], 1)


if __name__ == "__main__":
    unittest.main()
