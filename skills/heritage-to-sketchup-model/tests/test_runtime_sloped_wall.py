import copy
import importlib.util
import sys
import unittest
from pathlib import Path

try:
    from tests.test_contract_validation import FourStageContractTests
except ImportError:
    from test_contract_validation import FourStageContractTests

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'scripts'
sys.path.insert(0, str(RUNTIME))
spec = importlib.util.spec_from_file_location('runtime_contract_validation', RUNTIME / 'contract_validation.py')
validator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = validator
spec.loader.exec_module(validator)


class RuntimeSlopedWallTests(unittest.TestCase):
    def setUp(self):
        self.fixture = FourStageContractTests(methodName='test_all_four_valid_contracts_pass')
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def profile_errors(self, height):
        data = copy.deepcopy(self.fixture.topology)
        wall = data['walls'][0]
        length = sum((a-b)**2 for a,b in zip(wall['start'],wall['end']))**0.5
        wall['top_profile'] = [[0,height],[length,height+100]]
        return [i for i in validator.validate_contract(self.fixture.project,'building-topology',data)
                if i.code == 'topology.wall_top_profile_height']

    def test_measured_profile_can_cross_nominal_eave(self):
        self.assertFalse(self.profile_errors(self.fixture.topology['levels'][0]['top_elevation_mm']-20))

    def test_profile_cannot_cross_floor_base(self):
        self.assertTrue(self.profile_errors(self.fixture.topology['levels'][0]['elevation_mm']-20))

    def test_column_below_base_is_rejected(self):
        data=copy.deepcopy(self.fixture.topology)
        data['columns']=[dict(id='COL-TEST',level_id=data['levels'][0]['id'],footprint=[[0,0],[200,0],[200,200],[0,200]],
                             base_elevation_mm=0,top_elevation_mm=-1,evidence=data['walls'][0]['evidence'])]
        errors=validator.validate_contract(self.fixture.project,'building-topology',data)
        self.assertTrue(any(i.code=='topology.column_height' for i in errors))


if __name__ == '__main__':
    unittest.main()
