import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location('runtime_scope_audit',SCRIPTS/'audit_building_topology.py')
audit=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=audit
spec.loader.exec_module(audit)

class ScopeDeferralTests(unittest.TestCase):
    def check(self,key,stale=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            proof=root/'confirmation.md'
            proof.write_text('User confirmed materials deferred for white model.',encoding='utf8')
            digest=hashlib.sha256(proof.read_bytes()).hexdigest()
            review={'confirmation_boundary':{'must_review':{key:{'white_model_status':'user_deferred',
                'scope_confirmation':{'path':proof.name,'sha256':'0'*64 if stale else digest}}}}}
            return audit.scope_deferral_errors(root,review)

    def test_explicit_material_scope_is_allowed(self):
        self.assertFalse(self.check('material_zone_boundaries'))

    def test_stale_scope_is_rejected(self):
        self.assertTrue(self.check('material_zone_boundaries',True))

    def test_geometry_cannot_use_material_exception(self):
        self.assertTrue(self.check('canopies'))
