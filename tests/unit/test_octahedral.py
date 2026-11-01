"""Check the NVIDIA hemispherical normal encoding and its input conventions."""
import unittest
import numpy as np
from rtx_remix_importer.nvidia_core.octahedral import convert_float_to_octahedral_in_place


class TestOctahedral(unittest.TestCase):
    def test_reference_vectors_and_inward(self):
        p = np.array([[[.5, .5, 1, 1], [.8, .5, .9, 1], [.5, .5, 0, 1], [.5, .5, .5, 0]]], dtype=np.float32)
        self.assertEqual(convert_float_to_octahedral_in_place(p, False), 1)
        np.testing.assert_allclose(p[0, 0], [.5, .5, 0, 1], atol=1e-6)
        np.testing.assert_allclose(p[0, 1], [5/7, 5/7, 0, 1], atol=1e-6)
        np.testing.assert_allclose(p[0, 2:], [[.5, .5, 0, 1], [.5, .5, 0, 1]], atol=1e-6)

    def test_opengl_flips_green(self):
        ogl = np.array([[[.5, .8, .9, 1]]], dtype=np.float32)
        dx = np.array([[[.5, .2, .9, 1]]], dtype=np.float32)
        convert_float_to_octahedral_in_place(ogl, True)
        convert_float_to_octahedral_in_place(dx, False)
        np.testing.assert_allclose(ogl, dx, atol=1e-6)

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            convert_float_to_octahedral_in_place(np.zeros((4, 4, 3), dtype=np.float32), False)
        with self.assertRaises(ValueError):
            convert_float_to_octahedral_in_place(np.zeros((4, 4, 4), dtype=np.uint8), False)
