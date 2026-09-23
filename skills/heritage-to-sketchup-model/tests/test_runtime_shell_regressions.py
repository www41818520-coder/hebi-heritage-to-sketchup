import unittest
from pathlib import Path

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'

class RuntimeShellRegressions(unittest.TestCase):
    def test_white_and_production_share_roof_shell_algorithm(self):
        for name in ('build_topology_white_model.rb','build_sketchup_production_model.rb'):
            text=(SCRIPTS/name).read_text(encoding='utf-8')
            self.assertIn('HEBIRoofShellGeometry.build',text)
            self.assertNotIn("offset.length = mm(host.fetch('thickness_mm')",text)

    def test_white_ring_uses_solid_union_not_overlapping_exploded_panels(self):
        text=(SCRIPTS/'build_topology_white_model.rb').read_text(encoding='utf-8')
        self.assertIn('merged.union(temporary)',text)
        self.assertIn("raise 'Exterior wall ring is not a closed solid'",text)

    def test_export_restores_visibility_in_ensure(self):
        text=(SCRIPTS/'build_topology_white_model.rb').read_text(encoding='utf-8')
        self.assertIn('ensure\n        section_plane.erase!',text)

if __name__=='__main__': unittest.main()
