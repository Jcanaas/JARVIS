import tempfile
import unittest
from pathlib import Path

from actions.bios import find_ps2_bios, import_ps2_bios


class Ps2BiosLayoutTests(unittest.TestCase):
    def test_import_uses_directory_expected_by_lrps2(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "SCPH-70012.bin"
            source.write_bytes(b"\0" * (4 * 1024 * 1024))
            system_dir = root / "system"

            imported = import_ps2_bios(source, system_dir)

            self.assertEqual(
                imported,
                system_dir / "pcsx2" / "bios" / source.name,
            )
            self.assertEqual(find_ps2_bios(system_dir), imported)


if __name__ == "__main__":
    unittest.main()
