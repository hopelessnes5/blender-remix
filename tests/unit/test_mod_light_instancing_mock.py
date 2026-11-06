"""Standalone behavioural test of ModFileLoader's actual light-linking method.

Extracts methods from the source AST so the same runtime body is executed
against fake Blender/Usd objects without requiring a Blender installation.
"""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'rtx_remix_importer/ui/operators/mod_loader.py'
HELPER = ROOT / 'rtx_remix_importer/light_sharing.py'
spec = importlib.util.spec_from_file_location('light_sharing_mock_test', HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class Data:
    serial = 0
    def __init__(self, typ):
        self.type = typ
        self.serial = Data.serial
        Data.serial += 1
        self.energy = 10
        self.objects = []
    @property
    def users(self):
        return sum(x.data is self for x in MOCK_OBJECTS.items)
    def as_pointer(self): return self.serial


class Matrix:
    @staticmethod
    def Identity(_n): return Matrix()
    def __matmul__(self, _other): return Matrix()


class MockObj(dict):
    def __init__(self, name, data=None, typ='LIGHT', **tags):
        super().__init__(tags)
        self.name, self.data, self.type = name, data, typ
        self.matrix_world = Matrix()
        self.users_collection = []
        self.parent = None
        self.matrix_parent_inverse = Matrix()


class Objects:
    def __init__(self): self.items = []
    def new(self, name, object_data):
        obj = MockObj(name, object_data)
        self.items.append(obj)
        return obj
    def remove(self, obj, do_unlink=True):
        self.items[:] = [candidate for candidate in self.items if candidate is not obj]


class Lights:
    def remove(self, data):
        assert data.users == 0


MOCK_OBJECTS = Objects()


class Prim:
    def __init__(self, path, energy=10, typ='SphereLight'):
        self.path, self.energy, self.typ = path, energy, typ
    def GetPath(self): return self.path
    def GetName(self): return self.path.rsplit('/', 1)[-1]
    def IsValid(self): return True
    def GetTypeName(self): return self.typ
    def GetAttribute(self, name):
        return next((x for x in self.GetAttributes() if x.GetName() == name), None)
    def GetAttributes(self):
        return [Attribute('inputs:intensity', self.energy), Attribute('inputs:shaping:cone:angle', 40.)]


class Attribute:
    def __init__(self, name, value): self.name, self.value = name, value
    def GetName(self): return self.name
    def Get(self, _t=None): return self.value
    def IsValid(self): return True
    def HasAuthoredValue(self): return True


class Stage:
    def __init__(self, mapping): self.mapping = mapping
    def GetPrimAtPath(self, path): return self.mapping.get(str(path))


class Const:
    PROP_USD_PROTOTYPE_PATH = 'usd_prototype_path'


class Scene:
    def __init__(self): self.objects = MOCK_OBJECTS.items


class Collection:
    def __init__(self): self.objects = self
    def link(self, obj): return None


class Bpy:
    path = SimpleNamespace(clean_name=lambda s: s)
    data = SimpleNamespace(objects=MOCK_OBJECTS, lights=Lights())


def load_real_methods():
    tree = ast.parse(SRC.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ModFileLoader')
    chosen = {'_light_data_identity', '_desired_blender_light_type', '_sync_prototype_light'}
    cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in chosen]
    cls.bases, cls.keywords, cls.decorator_list = [], [], []
    scope = {
        'bpy': Bpy, 'mathutils': SimpleNamespace(Matrix=Matrix), 'constants': Const,
        'sphere_cone_parameters': helper.sphere_cone_parameters,
        'light_parameter_signature': helper.light_parameter_signature,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(SRC), 'exec'), scope)
    return scope['ModFileLoader']


class TestLinkBehaviour(unittest.TestCase):
    def setUp(self):
        MOCK_OBJECTS.items.clear()
        Loader = load_real_methods()
        self.loader = Loader()
        self.loader.context = SimpleNamespace(scene=Scene())
        self.loader._routed_light_instances = set()
        self.loader.stats = {'objects_created': 0}
        self.loader._prototype_light_relative_matrix = lambda *args: Matrix()
        self.loader._apply_visibility = lambda *args: None
        self.loader._apply_light_properties = lambda prim, obj: setattr(obj.data, 'energy', prim.energy)
        self.loader._target_collection = lambda: Collection()
        self.loader._mod_collection_label = lambda: 'mod test'
        self.loader.report = lambda *args: self.fail(f'Light placement failed: {args}')
        self.loader.time_code = None
        self.loader._create_light_object = lambda prim: MOCK_OBJECTS.new(prim.GetName(), Data(
            self.loader._desired_blender_light_type(prim)))
        H = 'ABCDEF0123456789'
        self.proto = f'/RootNode/meshes/mesh_{H}'
        self.src = self.proto + '/ref_abc/Lamp'
        self.instances = [f'/RootNode/instances/inst_{H}_{i}' for i in range(2)]
        self.anchors = {inst: MockObj('Mesh' + str(i), typ='MESH')
                        for i, inst in enumerate(self.instances)}
        self.loader.context.scene.objects.extend(self.anchors.values())
        self.loader._instance_anchor_objects = lambda _proto: self.anchors
        self.source = Prim(self.src)
        self.prototype = Prim(self.proto, typ='Xform')
        self.mapped = {inst: Prim(inst + '/ref_abc/Lamp') for inst in self.instances}
        self.loader._get_light_stage = lambda: Stage({p.path: p for p in self.mapped.values()})

    def get_lights(self):
        return [obj for obj in MOCK_OBJECTS.items if obj.type == 'LIGHT']

    def test_two_object_instances_link_same_data_and_repair_old(self):
        self.loader._sync_prototype_light(self.source, self.prototype)
        lights = self.get_lights()
        self.assertEqual(len(lights), 2)
        self.assertIs(lights[0].data, lights[1].data)
        lights[0].data.energy = 55
        self.assertEqual(lights[1].data.energy, 55)
        # Simulate old imported copies and re-apply the mod.
        lights[1].data = Data('SPOT')
        self.loader._routed_light_instances.clear()
        self.loader._sync_prototype_light(self.source, self.prototype)
        self.assertIs(lights[0].data, lights[1].data)
        self.assertEqual(len(self.get_lights()), 2)

    def test_per_instance_authored_override_splits_datablocks(self):
        self.mapped[self.instances[1]].energy = 25
        self.loader._sync_prototype_light(self.source, self.prototype)
        lights = self.get_lights()
        self.assertEqual(len(lights), 2)
        self.assertIsNot(lights[0].data, lights[1].data)

    def test_existing_point_light_is_retyped_to_spot(self):
        self.loader._sync_prototype_light(self.source, self.prototype)
        lights = self.get_lights()
        for light in lights:
            light.data = Data('POINT')
        self.loader._routed_light_instances.clear()
        self.loader._sync_prototype_light(self.source, self.prototype)
        self.assertEqual(len(self.get_lights()), 2)
        self.assertIs(lights[0].data, lights[1].data)
        self.assertEqual(lights[0].data.type, 'SPOT')


if __name__ == '__main__':
    unittest.main()
