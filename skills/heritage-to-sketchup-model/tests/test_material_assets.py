import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts/material_assets.py'
SPEC = importlib.util.spec_from_file_location('material_assets_test', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MaterialAssetTests(unittest.TestCase):
    def test_hash_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'sample.skm'
            path.write_bytes(b'fixture')
            spec = {'skm_asset': {'path': 'sample.skm', 'sha256': hashlib.sha256(b'fixture').hexdigest()}}
            self.assertEqual([], MODULE.validate_material_asset(root, spec))
            path.write_bytes(b'changed')
            self.assertTrue(MODULE.validate_material_asset(root, spec))

    def test_missing_asset_and_legacy(self):
        self.assertEqual([], MODULE.validate_material_asset(Path('.'), {}))
        self.assertTrue(MODULE.validate_material_asset(Path('.'), {'skm_asset': {'path':'missing.skm'}}))


if __name__ == '__main__':
    unittest.main()
