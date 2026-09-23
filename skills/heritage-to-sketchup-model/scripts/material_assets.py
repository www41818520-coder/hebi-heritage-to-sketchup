"""Check optional local SKM content binding before production."""
import hashlib
from pathlib import Path


def validate_material_asset(project, spec):
    asset = spec.get('skm_asset')
    if asset is None:
        return []
    if not isinstance(asset, dict):
        return ['SKM asset must be an object']
    path = Path(str(asset.get('path') or ''))
    if not path.is_absolute():
        path = project / path
    if path.suffix.lower() != '.skm' or not path.is_file():
        return ['SKM asset file is missing or has wrong extension']
    if hashlib.sha256(path.read_bytes()).hexdigest() != str(asset.get('sha256') or '').lower():
        return ['SKM asset hash does not match']
    return []
