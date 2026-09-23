import sys
from pathlib import Path
import unittest
import copy

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from access_geometry import convex_ramp_shell, mesh_errors


class AccessGeometryTests(unittest.TestCase):
    def record(self):
        return dict(shell_faces=convex_ramp_shell([(0,0),(7000,0),(7000,-1600),(0,-1600)],[(500,0),(6500,0)],-200,0),lower_elevation_mm=-200,upper_elevation_mm=0)

    def test_measured_flared_ramp_is_closed(self):
        self.assertEqual(mesh_errors(self.record()),[])

    def test_open_shell_fails(self):
        r=self.record(); r['shell_faces'].pop()
        self.assertTrue(mesh_errors(r))

    def test_wrong_grade_fails(self):
        r=self.record(); r['lower_elevation_mm']=-20
        self.assertTrue(mesh_errors(r))

    def test_inverted_face_fails(self):
        r=self.record(); r['shell_faces'][0].reverse()
        self.assertTrue(mesh_errors(r))


if __name__=='__main__':
    unittest.main()
