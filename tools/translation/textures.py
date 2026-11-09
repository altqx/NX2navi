"""Textures of BNTX files to RGBA images and back, for the formats a translation can write again: BC4 (one channel,
which the game's lettered textures use; shown as white with the channel as alpha) and RGBA8. The layout and BC4
decode are tools/ui_textures.py's (bntx_textures, deswizzle, bc4); this adds their inverses."""
import numpy as np

import ui_textures as ut  # tools/ is on the path of tools/translate.py

WRITABLE = {0x0b: 'RGBA8', 0x1d: 'BC4'}


def textures(data):
    return ut.bntx_textures(data)


def kind_name(tex):
    return ut.KINDS.get(tex['fmt'] >> 8, (f'format {tex["fmt"]:#06x}',))[0]


def _geometry(tex):
    _, bs, bpb = ut.KINDS[tex['fmt'] >> 8]
    wb, hb = (tex['w'] + bs - 1) // bs, (tex['h'] + bs - 1) // bs
    return wb, hb, bpb


def decode(tex, data):
    """The first mip level as an (h, w, 4) uint8 array; None for a format this cannot write back."""
    if tex['fmt'] >> 8 not in WRITABLE:
        return None
    wb, hb, bpb = _geometry(tex)
    lin = ut.deswizzle(tex, data, wb * bpb, hb)
    if tex['fmt'] >> 8 == 0x1d:
        return ut.bc4(lin, wb, hb, tex['w'], tex['h'])
    return lin.reshape(tex['h'], tex['w'], 4).copy()


def _addresses(tex, row, rows):
    """Byte address of each 16-byte unit of the linear rows in the block-linear layout (ut.deswizzle's mapping)."""
    th = tex['th']
    gobs, unit, block = (row + 63) // 64, 8 << th, 512 << th
    cols = (row + 15) // 16
    y = np.arange(rows)[:, None]
    x = (np.arange(cols) * 16)[None, :]
    return (y // unit) * gobs * block + (x // 64) * block + ((y % unit) // 8) * 512 + \
        (x % 64 // 32) * 256 + (y % 8 // 4) * 128 + (x % 32 // 16) * 64 + (y % 4) * 16


def swizzle_into(tex, data, lin):
    """data (a BNTX file) with the first level of tex replaced by lin, (rows, row bytes) linear; the layout's padding
    keeps its old bytes."""
    rows, row = lin.shape
    at = _addresses(tex, row, rows)
    cols = at.shape[1]
    padded = np.zeros((rows, cols * 16), dtype=np.uint8)
    padded[:, :row] = lin
    units = padded.reshape(rows, cols, 16)
    size = tex['size']
    level = np.frombuffer(bytes(data[tex['data']:tex['data'] + size]), dtype=np.uint8).copy()
    total = (max(int(at.max()) + 16, size) + 15) & ~15
    level = np.concatenate([level, np.zeros(total - size, dtype=np.uint8)])
    flat = at.reshape(-1)
    level.reshape(-1, 16)[flat // 16] = units.reshape(-1, 16)
    out = bytearray(data)
    out[tex['data']:tex['data'] + size] = level[:size].tobytes()
    return bytes(out)


def bc4_encode(channel):
    """(h, w) uint8 -> BC4 blocks, (hb, wb * 8) uint8: the block's highest and lowest value as the endpoints (the
    eight-value mode) and each pixel's nearest of the eight."""
    h, w = channel.shape
    hb, wb = (h + 3) // 4, (w + 3) // 4
    full = np.zeros((hb * 4, wb * 4), dtype=np.uint8)
    full[:h, :w] = channel
    if h % 4:
        full[h:, :w] = channel[h - 1:h, :]
    if w % 4:
        full[:, w:] = full[:, w - 1:w]
    px = full.reshape(hb, 4, wb, 4).transpose(0, 2, 1, 3).reshape(hb, wb, 16).astype(np.int32)
    a, b = px.max(axis=2), px.min(axis=2)
    pal = np.zeros((hb, wb, 8), dtype=np.int32)
    pal[..., 0], pal[..., 1] = a, b
    for i in range(6):
        pal[..., 2 + i] = ((6 - i) * a + (i + 1) * b) // 7
    sel = np.abs(px[..., :, None] - pal[..., None, :]).argmin(axis=3)
    sel = np.where((a == b)[..., None], 0, sel)          # one value: both endpoints equal, every index 0
    bits = np.zeros((hb, wb), dtype=np.uint64)
    for i in range(16):
        bits |= sel[..., i].astype(np.uint64) << np.uint64(3 * i)
    out = np.zeros((hb, wb, 8), dtype=np.uint8)
    out[..., 0], out[..., 1] = a, b
    for i in range(6):
        out[..., 2 + i] = ((bits >> np.uint64(8 * i)) & np.uint64(0xff)).astype(np.uint8)
    return out.reshape(hb, wb * 8)


def mask(rgba):
    """The one channel a BC4 texture keeps of an image: its alpha when any pixel is transparent (white letters on
    transparency, as exported), else its brightness (white letters on black)."""
    if (rgba[..., 3] < 255).any():
        return rgba[..., 3]
    return (rgba[..., :3].astype(np.uint32) @ np.array([77, 150, 29], dtype=np.uint32) >> 8).astype(np.uint8)


def encode(tex, data, rgba):
    """data (a BNTX file) with tex's first level drawn from rgba, (h, w, 4) uint8 at the texture's size. BC4 takes the
    alpha channel when the image has any transparency, else the brightness."""
    if rgba.shape[:2] != (tex['h'], tex['w']):
        raise ValueError(f'{tex["name"]}: the image is {rgba.shape[1]}x{rgba.shape[0]}, the texture {tex["w"]}x{tex["h"]}')
    kind = tex['fmt'] >> 8
    if kind == 0x1d:
        return swizzle_into(tex, data, bc4_encode(mask(rgba)))
    if kind == 0x0b:
        return swizzle_into(tex, data, rgba.reshape(tex['h'], tex['w'] * 4))
    raise ValueError(f'{tex["name"]}: {kind_name(tex)} textures cannot be written yet')
