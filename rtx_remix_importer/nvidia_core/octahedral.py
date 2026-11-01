"""RTX Remix hemispherical octahedral normal-map conversion.

Adapted from omni.flux.utils.octahedral_converter.octahedral_converter_core.

SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    https://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import numpy as np

_CHUNK_PIXELS = 1 << 16


def convert_float_to_octahedral_in_place(pixels: np.ndarray, opengl: bool) -> int:
    """Convert float RGBA normals in place; return the count of inward normals.

    RGB inputs and RG outputs are in [0, 1]. The output B and A are 0 and 1.
    OpenGL inputs have their green axis flipped to the Remix convention.
    """
    if not isinstance(pixels, np.ndarray) or pixels.ndim != 3 or pixels.shape[2] != 4:
        raise ValueError('pixels must have shape (height, width, 4)')
    if pixels.dtype != np.float32 or not pixels.flags.writeable:
        raise ValueError('pixels must be a writable float32 array')
    if not pixels.shape[1]:
        return 0
    rows_per_chunk = max(1, _CHUNK_PIXELS // pixels.shape[1])
    y_scale, y_offset = (-2.0, 1.0) if opengl else (2.0, -1.0)
    inward = 0
    for start in range(0, pixels.shape[0], rows_per_chunk):
        rows = pixels[start:start + rows_per_chunk]
        x = rows[:, :, 0] * 2.0
        x -= 1.0
        y = rows[:, :, 1] * y_scale
        y += y_offset
        z = rows[:, :, 2] * 2.0
        z -= 1.0
        inward += int(np.count_nonzero(z < 0.0))
        np.abs(z, out=z)
        scale = np.abs(x)
        scale += np.abs(y)
        scale += z
        scale[scale == 0.0] = np.inf
        np.divide(0.5, scale, out=scale)
        np.subtract(x, y, out=z)
        x += y
        x *= scale
        x += 0.5
        z *= scale
        z += 0.5
        rows[:, :, 0] = x
        rows[:, :, 1] = z
        rows[:, :, 2] = 0.0
        rows[:, :, 3] = 1.0
    return inward
