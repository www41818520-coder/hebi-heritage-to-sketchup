import importlib.util
import sys
import unittest
from pathlib import Path

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration
from ezdxf.addons.drawing.recorder import Recorder, PointsRecord

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("runtime_reader", SCRIPTS / "create_frame_confirmation_pages.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class ReadingMaskTests(unittest.TestCase):
    def test_nested_wipeout_stays_white_and_line_stays_black(self):
        doc = ezdxf.new()
        block = doc.blocks.new("WINDOW")
        line = block.add_line((5, 0), (5, 10), dxfattribs={"color": 3})
        mask = block.add_wipeout([(0, 0), (10, 0), (10, 10), (0, 10)])
        block.set_redraw_order([(mask.dxf.handle, "1"), (line.dxf.handle, "2")])
        line.transparency = 0.8
        source_attributes = (mask.dxfattribs().copy(), line.dxfattribs().copy())
        ref = doc.modelspace().add_blockref("WINDOW", (100, 100))
        recorder = Recorder()
        frontend = reader.create_reading_frontend(RenderContext(doc), recorder,
                            Configuration(background_policy=BackgroundPolicy.WHITE))
        frontend.draw_layout(doc.modelspace())
        polygons = [r for r in recorder.records if isinstance(r, PointsRecord) and len(r.points) > 2]
        lines = [r for r in recorder.records if isinstance(r, PointsRecord) and len(r.points) == 2]
        self.assertTrue(polygons)
        self.assertTrue(lines)
        self.assertLess(recorder.records.index(polygons[0]), recorder.records.index(lines[0]))
        self.assertTrue(all(recorder.properties[r.property_hash].color == "#ffffff" for r in polygons))
        self.assertTrue(all(recorder.properties[r.property_hash].color == "#000000" for r in lines))
        self.assertTrue(all(r.handle == ref.dxf.handle for r in polygons + lines))
        self.assertEqual(source_attributes, (mask.dxfattribs(), line.dxfattribs()))


if __name__ == "__main__":
    unittest.main()
