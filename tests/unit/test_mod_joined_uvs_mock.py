"""Test the actual ModFileLoader UV method with minimal USD/Blender stand-ins.

Does not import bpy or pxr, so it runs on machines without Blender.
"""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


SRC = Path(__file__).resolve().parents[2] / 'rtx_remix_importer' / 'ui' / 'operators' / 'mod_loader.py'
tree = ast.parse(SRC.read_text(encoding='utf-8'))
loader = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ModFileLoader')
method = next(node for node in loader.body if isinstance(node, ast.FunctionDef) and node.name == '_apply_joined_mesh_uvs')
method.decorator_list = []
ns = {
    'bpy': SimpleNamespace(types=SimpleNamespace(Mesh=object)),
    'UsdGeom': SimpleNamespace(Tokens=SimpleNamespace(
        faceVarying='faceVarying', vertex='vertex', varying='varying',
        constant='constant', uniform='uniform')),
}
exec(compile(ast.Module(body=[method], type_ignores=[]), str(SRC), 'exec'), ns)
apply_joined_mesh_uvs = ns['_apply_joined_mesh_uvs']


class UVData:
    def __init__(self, count):
        self.values = [(0., 0.)] * count

    def foreach_set(self, attr, floats):
        self.values = list(zip(*[iter(floats)] * 2))


class UVLayers(list):
    def __init__(self, mesh):
        super().__init__()
        self.mesh = mesh

    def new(self, name):
        layer = SimpleNamespace(name=name, data=UVData(len(self.mesh.loops)))
        self.append(layer)
        return layer


class FakeMesh:
    def __init__(self, faces):
        self.polygons = []
        self.loops = []
        for vertices in faces:
            start = len(self.loops)
            for vertex in vertices:
                self.loops.append(SimpleNamespace(index=len(self.loops), vertex_index=vertex))
            self.polygons.append(SimpleNamespace(loop_start=start, loop_total=len(vertices)))
        self.uv_layers = UVLayers(self)


class TestJoinedUVs(unittest.TestCase):
    def test_second_part_uses_start_vertex_offset(self):
        mesh = FakeMesh([(0, 1, 2), (3, 4, 5)])
        parts = [
            (([(0., 0.), (1., 0.), (0., 1.)], None, 'vertex'), 0, 0, 3),
            (([(0.1, 0.2), (0.9, 0.2), (0.1, 0.8)], None, 'vertex'), 3, 3, 6),
        ]
        self.assertTrue(apply_joined_mesh_uvs(None, mesh, parts))
        self.assertEqual(len(mesh.uv_layers), 1)
        values = mesh.uv_layers[0].data.values
        self.assertAlmostEqual(values[0][1], 1.0)
        self.assertAlmostEqual(values[3][0], 0.1)
        self.assertAlmostEqual(values[3][1], 0.8)
        self.assertAlmostEqual(values[4][0], 0.9)

    def test_missing_uv_part_creates_no_uv_layer(self):
        mesh = FakeMesh([(0, 1, 2), (3, 4, 5)])
        parts = [
            (([(0., 0.)] * 3, None, 'faceVarying'), 0, 0, 3),
            (None, 3, 3, 6),
        ]
        self.assertFalse(apply_joined_mesh_uvs(None, mesh, parts))
        self.assertEqual(len(mesh.uv_layers), 0)

    def test_invalid_facevarying_indices_do_not_create_uv_layer(self):
        mesh = FakeMesh([(0, 1, 2)])
        parts = [(([(0., 0.)], [0, 0], 'faceVarying'), 0, 0, 3)]
        self.assertFalse(apply_joined_mesh_uvs(None, mesh, parts))
        self.assertEqual(len(mesh.uv_layers), 0)

    def test_uniform_per_face(self):
        mesh = FakeMesh([(0, 1, 2), (3, 4, 5)])
        parts = [(([(0.2, 0.3), (0.7, 0.8)], None, 'uniform'), 0, 0, 6)]
        self.assertTrue(apply_joined_mesh_uvs(None, mesh, parts))
        self.assertEqual(mesh.uv_layers[0].data.values[0], (0.2, 0.7))
        self.assertAlmostEqual(mesh.uv_layers[0].data.values[-1][0], 0.7)
        self.assertAlmostEqual(mesh.uv_layers[0].data.values[-1][1], 0.2)


if __name__ == '__main__':
    unittest.main()
