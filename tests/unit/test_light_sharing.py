"""Blender-independent tests for USD light datablock sharing and cone detection.

Run: python -m unittest discover -s tests/unit -p test_light_sharing.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

MOD = Path(__file__).resolve().parents[2] / 'rtx_remix_importer' / 'light_sharing.py'
spec = importlib.util.spec_from_file_location('remix_light_sharing_test', MOD)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Attribute:
    def __init__(self, name, value, authored=True):
        self.name, self.value, self.authored = name, value, authored

    def IsValid(self): return True
    def HasAuthoredValue(self): return self.authored
    def Get(self, _time=None): return self.value
    def GetName(self): return self.name


class Stage:
    def __init__(self, paths): self.paths = set(paths)
    def GetPrimAtPath(self, path): return Prim(path, self) if path in self.paths else InvalidPrim()


class InvalidPrim:
    def IsValid(self): return False


class Prim:
    def __init__(self, path, stage=None, attrs=None, kind='SphereLight'):
        self.path, self.stage, self.attrs, self.kind = path, stage, (attrs or {}), kind
    def IsValid(self): return True
    def GetAttribute(self, name): return self.attrs.get(name)
    def GetAttributes(self): return list(self.attrs.values())
    def GetPath(self): return self.path
    def GetStage(self): return self.stage
    def GetTypeName(self): return self.kind


HASH = 'ABCDEF0123456789'
PROTOTYPE = f'/RootNode/meshes/mesh_{HASH}/ref_abc/Lamp'
INST0 = f'/RootNode/instances/inst_{HASH}_0/ref_abc/Lamp'
INST1 = f'/RootNode/instances/inst_{HASH}_1/ref_abc/Lamp'


class TestUsdLightSharing(unittest.TestCase):
    def test_cone_without_applied_schema_is_spot(self):
        prim = Prim('/Light', attrs={
            'inputs:shaping:cone:angle': Attribute('inputs:shaping:cone:angle', 32.0),
            'inputs:shaping:cone:softness': Attribute('inputs:shaping:cone:softness', 0.2),
        })
        shaped, angle, softness = mod.sphere_cone_parameters(prim)
        self.assertTrue(shaped)
        self.assertEqual((angle, softness), (32.0, 0.2))
        self.assertAlmostEqual(mod.cone_spot_size(angle), 64.0 * 3.141592653589793 / 180.0)

    def test_applied_schema_without_authored_properties_is_point(self):
        prim = Prim('/Light', attrs={
            'inputs:shaping:cone:angle': Attribute('inputs:shaping:cone:angle', 45.0, authored=False),
        })
        self.assertFalse(mod.sphere_cone_parameters(prim)[0])
        self.assertTrue(mod.sphere_cone_parameters(Prim('/Light'))[0] is False)

    def test_bare_shaping_namespace_and_softness_only(self):
        prim = Prim('/Light', attrs={'shaping:cone:softness': Attribute('shaping:cone:softness', 0.4)})
        self.assertEqual(mod.sphere_cone_parameters(prim), (True, 45.0, 0.4))

    def test_parameter_signature_does_not_include_transform(self):
        one = Prim(INST0, attrs={
            'inputs:intensity': Attribute('inputs:intensity', 2.5),
            'xformOp:translate': Attribute('xformOp:translate', (1, 0, 0)),
        })
        two = Prim(INST1, attrs={
            'inputs:intensity': Attribute('inputs:intensity', 2.5),
            'xformOp:translate': Attribute('xformOp:translate', (100, 0, 0)),
        })
        self.assertEqual(mod.light_parameter_signature(one), mod.light_parameter_signature(two))
        two.attrs['inputs:intensity'] = Attribute('inputs:intensity', 4.0)
        self.assertNotEqual(mod.light_parameter_signature(one), mod.light_parameter_signature(two))

    def test_instance_paths_resolve_same_prototype(self):
        stage = Stage((PROTOTYPE,))
        self.assertEqual(mod.prototype_light_path(Prim(INST0, stage)), PROTOTYPE)
        self.assertEqual(mod.prototype_light_path(Prim(INST1, stage)), PROTOTYPE)
        self.assertEqual(mod.prototype_light_path(Prim('/RootNode/lights/light_ABCDEF0123456789', stage)),
                         '/RootNode/lights/light_ABCDEF0123456789')


if __name__ == '__main__':
    unittest.main()
