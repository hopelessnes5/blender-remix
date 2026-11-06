"""Unit regressions for RTX Remix sky_ names and prototype-owned Sun import.

Exercises the actual routing helpers from mod_loader.py with fake Blender data.
No bpy/pxr installation required. A Blender integration test covers USD composition.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'rtx_remix_importer/ui/operators/mod_loader.py'


class SkyObject(dict):
    def __init__(self, name, obj_type='MESH', data_name=None, **tags):
        super().__init__(tags)
        self.name = name
        self.type = obj_type
        self.data = SimpleNamespace(name=data_name or name, get=lambda key, default='': default)


class TestSkySunRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SRC.read_text(encoding='utf-8'))
        functions = {
            node.name: node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in {
                '_hash_tokens', '_sky_object_hashes', '_prototype_container_path',
                '_ancestor_prototype_path'}
        }
        scope = {
            're': __import__('re'),
            'Set': set,
            'Optional': __import__('typing').Optional,
        }
        for node in tree.body:
            if isinstance(node, ast.Assign):
                if any(isinstance(t, ast.Name) and t.id in {'_SKY_MESH_NAME', '_PROTOTYPE_PATH'}
                       for t in node.targets):
                    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(SRC), 'exec'), scope)
        exec(compile(ast.fix_missing_locations(ast.Module(body=list(functions.values()), type_ignores=[])), str(SRC), 'exec'), scope)
        cls.scope = scope
        cls.hash = '5E68DDDA519FEA31'

    def test_sky_name_is_scene_provenance_without_instance_id(self):
        sky = SkyObject('sky_' + self.hash)
        self.assertEqual(self.scope['_sky_object_hashes'](sky), {self.hash})
        self.assertEqual(self.scope['_sky_object_hashes'](SkyObject('Sky_' + self.hash + '.001')), {self.hash})
        self.assertEqual(self.scope['_sky_object_hashes'](SkyObject('unnamed', data_name='sky_' + self.hash + '_data')), {self.hash})

    def test_other_objects_do_not_become_sky_provenance(self):
        for obj in (SkyObject('mesh_' + self.hash), SkyObject('some_sky_' + self.hash),
                    SkyObject('sky_' + self.hash, obj_type='LIGHT'), SkyObject('sky_1234')):
            self.assertEqual(self.scope['_sky_object_hashes'](obj), set())

    def test_prototype_parent_accepts_both_sky_and_mesh_names(self):
        pred = self.scope['_prototype_container_path']
        ancestor = self.scope['_ancestor_prototype_path']
        for leaf in ('mesh_', 'sky_'):
            root = '/RootNode/meshes/' + leaf + self.hash
            self.assertTrue(pred(root), root)
            self.assertEqual(ancestor(root + '/ref_1234/Sun'), root)
        self.assertTrue(pred('/RootNode/sky/sky_' + self.hash))
        self.assertFalse(pred('/Unrelated/sky_' + self.hash))
        self.assertFalse(pred('/RootNode/meshes/ref_' + self.hash))

    def test_sky_name_detected_by_real_scene_mapping_methods(self):
        tree = ast.parse(SRC.read_text(encoding='utf-8'))
        source_cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ModFileLoader')
        names = {'_scene_relation_hashes', '_scene_objects_for_hashes'}
        source_cls.body = [n for n in source_cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
        source_cls.bases, source_cls.keywords, source_cls.decorator_list = [], [], []
        fake = SimpleNamespace(PROP_USD_INSTANCE_PATH='usd_instance_path',
                               PROP_USD_PRIM_PATH='usd_prim_path',
                               PROP_USD_PROTOTYPE_PATH='usd_prototype_path',
                               PROP_REMIX_MESH_HASH='remix_mesh_hash')
        sky = SkyObject('sky_' + self.hash)
        unrelated = SkyObject('SomeMesh')
        scope = dict(self.scope, bpy=SimpleNamespace(data=SimpleNamespace(objects=[sky, unrelated]), types=SimpleNamespace(Object=object)), constants=fake, List=list)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[source_cls], type_ignores=[])), str(SRC), 'exec'), scope)
        loader = scope['ModFileLoader']()
        self.assertIn(self.hash, loader._scene_relation_hashes())
        self.assertEqual(loader._scene_objects_for_hashes({self.hash}), [sky])


if __name__ == '__main__':
    unittest.main()
