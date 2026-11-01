"""Decode common capture DDS thumbnails in memory using Blender's NumPy.

The returned RGBA pixels are display data; no converted image file is written.
"""

import struct

import numpy as np


def _blocks(data, width, height, fmt):
    block_size = 8 if fmt == 'BC1' else 16
    bx, by = (width + 3) // 4, (height + 3) // 4
    required = bx * by * block_size
    if len(data) < required:
        raise ValueError('truncated DDS blocks')
    blocks = np.frombuffer(data, dtype=np.uint8, count=required).reshape(by, bx, block_size)
    colors = blocks[:, :, -8:].copy()
    c0 = colors[:, :, 0].astype(np.uint16) | (colors[:, :, 1].astype(np.uint16) << 8)
    c1 = colors[:, :, 2].astype(np.uint16) | (colors[:, :, 3].astype(np.uint16) << 8)

    def rgb565(c):
        r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
        return np.stack(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)), axis=-1).astype(np.uint16)

    a, b = rgb565(c0), rgb565(c1)
    four = (c0 > c1) if fmt == 'BC1' else np.ones((by, bx), dtype=bool)
    palette = np.empty((by, bx, 4, 4), dtype=np.uint16)
    palette[:, :, 0, :3], palette[:, :, 1, :3] = a, b
    palette[:, :, 2, :3] = np.where(four[..., None], (2*a+b)//3, (a+b)//2)
    palette[:, :, 3, :3] = np.where(four[..., None], (a+2*b)//3, 0)
    palette[:, :, :, 3] = 255
    palette[:, :, 3, 3] = np.where(four, 255, 0)
    indices = np.frombuffer(colors[:, :, 4:8].copy().tobytes(), dtype='<u4').reshape(by, bx)
    indices = (indices[..., None] >> (2 * np.arange(16, dtype=np.uint32))) & 3
    pixels = np.take_along_axis(palette, indices[..., None], axis=2).astype(np.uint8)

    if fmt == 'BC2':
        alpha_bytes = blocks[:, :, :8]
        alpha = np.empty((by, bx, 16), dtype=np.uint8)
        alpha[..., 0::2] = (alpha_bytes & 15) * 17
        alpha[..., 1::2] = (alpha_bytes >> 4) * 17
        pixels[..., 3] = alpha
    elif fmt == 'BC3':
        a0, a1 = blocks[:, :, 0].astype(np.uint16), blocks[:, :, 1].astype(np.uint16)
        alpha_palette = np.empty((by, bx, 8), dtype=np.uint16)
        alpha_palette[..., 0], alpha_palette[..., 1] = a0, a1
        for i in range(2, 8):
            interp8 = ((8-i)*a0 + (i-1)*a1)//7
            interp6 = ((6-i)*a0 + (i-1)*a1)//5 if i < 6 else (0 if i == 6 else 255)
            alpha_palette[..., i] = np.where(a0 > a1, interp8, interp6)
        packed = np.zeros((by, bx), dtype=np.uint64)
        for i in range(6):
            packed |= blocks[:, :, 2+i].astype(np.uint64) << (8*i)
        alpha_indices = (packed[..., None] >> (3*np.arange(16, dtype=np.uint64))) & 7
        pixels[..., 3] = np.take_along_axis(alpha_palette, alpha_indices, axis=2).astype(np.uint8)

    return pixels.reshape(by, bx, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(by*4, bx*4, 4)[:height, :width]


def read_dds(path):
    """Return top-to-bottom RGBA floats for an ordinary 2D DDS thumbnail."""
    with open(path, 'rb') as file:
        data = file.read()
    if len(data) < 128 or data[:4] != b'DDS ' or struct.unpack_from('<I', data, 4)[0] != 124:
        raise ValueError('invalid DDS header')
    height, width = struct.unpack_from('<II', data, 12)
    if not width or not height or width > 8192 or height > 8192:
        raise ValueError('invalid DDS dimensions')
    fourcc = data[84:88]
    offset = 128
    if fourcc == b'DX10':
        if len(data) < 148:
            raise ValueError('truncated DDS DX10 header')
        dxgi, dimension, misc, array_size, _ = struct.unpack_from('<5I', data, 128)
        if dimension != 3 or array_size != 1 or misc & 4:
            raise ValueError('DDS is not a 2D image')
        offset = 148
        fmt = {10: 'RGBA16F', 28: 'RGBA8', 29: 'RGBA8', 87: 'BGRA8', 91: 'BGRA8',
               71: 'BC1', 72: 'BC1', 74: 'BC2', 75: 'BC2', 77: 'BC3', 78: 'BC3'}.get(dxgi)
    else:
        fmt = {b'\x71\x00\x00\x00': 'RGBA16F', b'DXT1': 'BC1', b'DXT3': 'BC2', b'DXT5': 'BC3'}.get(fourcc)
        flags, bits = struct.unpack_from('<II', data, 80)[0], struct.unpack_from('<I', data, 88)[0]
        if not fmt and flags & 0x40 and bits == 32:
            masks = struct.unpack_from('<4I', data, 92)
            fmt = {(0xff, 0xff00, 0xff0000, 0xff000000): 'RGBA8',
                   (0xff0000, 0xff00, 0xff, 0xff000000): 'BGRA8',
                   (0xff0000, 0xff00, 0xff, 0): 'BGRX8'}.get(masks)
    if fmt in ('BC1', 'BC2', 'BC3'):
        pixels = _blocks(data[offset:], width, height, fmt).astype(np.float32) / 255
    elif fmt in ('RGBA8', 'BGRA8', 'BGRX8', 'RGBA16F'):
        dtype = '<f2' if fmt == 'RGBA16F' else 'u1'
        size = width * height * 4 * np.dtype(dtype).itemsize
        if len(data) - offset < size:
            raise ValueError('truncated DDS pixels')
        pixels = np.frombuffer(data, dtype=dtype, count=width*height*4, offset=offset).reshape(height, width, 4).astype(np.float32)
        if fmt != 'RGBA16F':
            pixels /= 255
        if fmt in ('BGRA8', 'BGRX8'):
            pixels = pixels[..., [2, 1, 0, 3]]
        if fmt == 'BGRX8':
            pixels[..., 3] = 1
    else:
        raise ValueError('unsupported DDS thumbnail format')
    return np.nan_to_num(pixels, nan=0.0, posinf=1.0, neginf=0.0).clip(0, 1)


def icon_pixels(path, size=256):
    """Fit the image into a square Blender icon, keeping its aspect ratio."""
    image = read_dds(path)
    height, width = image.shape[:2]
    scale = min(size / width, size / height)
    w, h = max(1, round(width * scale)), max(1, round(height * scale))
    ys = np.minimum((np.arange(h) / scale).astype(int), height - 1)
    xs = np.minimum((np.arange(w) / scale).astype(int), width - 1)
    icon = np.zeros((size, size, 4), dtype=np.float32)
    y, x = (size-h)//2, (size-w)//2
    icon[y:y+h, x:x+w] = image[ys[:, None], xs]
    return np.ascontiguousarray(icon[::-1]).ravel()
