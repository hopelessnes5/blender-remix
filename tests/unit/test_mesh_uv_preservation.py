"""Run without Blender: python -m unittest discover -s tests/unit -p test_mesh_uv_preservation.py"""

import unittest
from types import SimpleNamespace
import importlib.util
from pathlib import Path

# Load this bpy-independent module without importing the add-on package __init__.
module_file = Path(__file__).resolve().parents[2] / 'rtx_remix_importer' / 'mesh_uv_preservation.py'
spec = importlib.util.spec_from_file_location('remix_mesh_uv_preservation', module_file)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
matching_topology = module.matching_topology
same_uv_layout = module.same_uv_layout
copy_uv_layers_if_compatible = module.copy_uv_layers_if_compatible


class MockUVData:
    def __init__(self, coords):
        self.coords = list(coords)

    def foreach_get(self, field, values):
        assert field == 'uv'
        for index, uv in enumerate(self.coords):
            values[index * 2] = uv[0]
            values[index * 2 + 1] = uv[1]

    def foreach_set(self, field, values):
        assert field == 'uv'
        coords = list(values)
        self.coords = [(coords[i], coords[i + 1]) for i in range(0, len(coords), 2)]


class MockUVLayer:
    def __init__(self, name, coords):
        self.name = name
        self.data = MockUVData(coords)
        self.active_render = False


class MockUVCollection(list):
    active_index = 0

    def __init__(self, mesh, maps=None):
        super().__init__([])
        self.mesh = mesh
        for name, coords in (maps or {}).items():
            self.append(MockUVLayer(name, coords))

    def new(self, name):
        layer = MockUVLayer(name, [(0, 0)] * len(self.mesh.loops))
        self.append(layer)
        return layer


class MockMesh:
    def __init__(self, verts, faces, maps=None):
        self.vertices = [SimpleNamespace(index=i) for i in range(verts)]
        self.polygons = [SimpleNamespace(vertices=tuple(face)) for face in faces]
        self.loops = [SimpleNamespace(vertex_index=v) for face in faces for v in face]
        self.uv_layers = MockUVCollection(self, maps)


class TestMeshUvPreservation(unittest.TestCase):
    def test_identical_topology_restores_all_layers(self):
        source = MockMesh(4, [(0, 1, 2), (0, 2, 3)], {
            'st': [(0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1)],
            'lightmap': [(0.1, 0.1)] * 6,
        })
        source.uv_layers[1].active_render = True
        source.uv_layers.active_index = 1
        target = MockMesh(4, [(0, 1, 2), (0, 2, 3)])
        self.assertTrue(copy_uv_layers_if_compatible(source, target))
        self.assertEqual([layer.name for layer in target.uv_layers], ['st', 'lightmap'])
        self.assertEqual(target.uv_layers.active_index, 1)
        self.assertTrue(target.uv_layers[1].active_render)
        self.assertTrue(same_uv_layout(source, target))

    def test_changed_corner_order_must_not_transfer(self):
        source = MockMesh(3, [(0, 1, 2)], {'st': [(0, 0), (1, 0), (0, 1)]})
        target = MockMesh(3, [(0, 2, 1)])
        self.assertFalse(matching_topology(source, target))
        self.assertFalse(copy_uv_layers_if_compatible(source, target))
        self.assertEqual(len(target.uv_layers), 0)

    def test_differing_source_uvs_cannot_be_merged(self):
        a = MockMesh(3, [(0, 1, 2)], {'st': [(0, 0), (1, 0), (0, 1)]})
        b = MockMesh(3, [(0, 1, 2)], {'st': [(0, 0), (0, 1), (1, 0)]})
        self.assertFalse(same_uv_layout(a, b))

    def test_never_override_existing_target_uvs(self):
        source = MockMesh(3, [(0, 1, 2)], {'st': [(0, 0), (1, 0), (0, 1)]})
        dest = MockMesh(3, [(0, 1, 2)], {'authored': [(0.5, 0.5)] * 3})
        self.assertFalse(copy_uv_layers_if_compatible(source, dest))
        self.assertEqual(dest.uv_layers[0].name, 'authored')


if __name__ == '__main__':
    unittest.main()
