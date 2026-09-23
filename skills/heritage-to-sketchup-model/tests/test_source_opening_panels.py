import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts/opening_panels.py'
SPEC = importlib.util.spec_from_file_location('source_opening_panels', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SourcePaneTests(unittest.TestCase):
    def check(self, rects, **changes):
        spec = dict(panel_type='glass', mullion_ratios=[], transom_ratios=[], panel_rectangles_mm=rects)
        spec.update(changes)
        return MODULE.validate_panels(dict(type='window', width_mm=1800, height_mm=4800), spec)

    def test_unequal_source_rows(self):
        self.assertEqual([], self.check([[50,50,850,1750],[50,1850,850,2750],[950,50,1750,1750]]))

    def test_touching_and_duplicate(self):
        self.assertTrue(self.check([[50,50,850,1750],[850,50,1750,1750]]))
        self.assertTrue(self.check([[50,50,850,1750]]*2))

    def test_outside_and_degenerate(self):
        for rect in [[0,50,850,1750],[50,50,1900,1750],[50,50,50,1750],[50,50,float('nan'),1750]]:
            self.assertTrue(self.check([rect]))

    def test_conflicting_modes(self):
        self.assertTrue(self.check([[50,50,850,1750]], panel_type='solid'))
        self.assertTrue(self.check([[50,50,850,1750]], mullion_ratios=[0.5]))

    def test_legacy_unchanged(self):
        self.assertEqual([], MODULE.validate_panels({}, {}))


if __name__ == '__main__':
    unittest.main()
