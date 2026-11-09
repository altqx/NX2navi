"""tools/translation on small made-up files (no game data): SARC, MSBT and PO round trips, the markup, the check of
control codes, BC4 and the block-linear layout, and the Thai cluster precomposition.

Run: .venv-py314/bin/python tests/translation_test.py
"""
import struct
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from translation import fonts, msbt, po, sarc, textures  # noqa: E402


def make_sarc(members, align=0x10):
    """A SARC with members [(name, bytes)], names in order, each member at a multiple of align."""
    names = bytearray()
    attrs = []
    for name, _ in members:
        attrs.append(0x01000000 | len(names) // 4)
        names += name.encode() + b'\0'
        names += bytes(-len(names) % 4)
    head = 0x14 + 12 + 16 * len(members) + 8 + len(names)
    start = (head + 0xff) & ~0xff
    data, nodes = bytearray(), bytearray()
    for (name, body), attr in zip(members, attrs):
        data += bytes(-len(data) % align)
        nodes += struct.pack('<4I', 0, attr, len(data), len(data) + len(body))
        data += body
    out = bytearray(b'SARC' + struct.pack('<HHIIHH', 0x14, 0xfeff, 0, start, 0x100, 0))
    out += b'SFAT' + struct.pack('<HHI', 12, len(members), 0x65) + nodes + b'SFNT' + struct.pack('<HH', 8, 0) + names
    out += bytes(start - len(out)) + data
    struct.pack_into('<I', out, 8, len(out))
    return bytes(out)


def make_msbt(messages, labels):
    """A UTF-8 MSBT with LBL1 (one hash slot) and TXT2."""
    lbl = bytearray(struct.pack('<III', 1, len(labels), 12))
    for i, name in enumerate(labels):
        lbl += bytes([len(name)]) + name.encode() + struct.pack('<I', i)
    txt = bytearray(struct.pack('<I', len(messages)))
    body = bytearray()
    for m in messages:
        txt += struct.pack('<I', 4 + 4 * len(messages) + len(body))
        body += m + b'\0'
    txt += body
    out = bytearray(b'MsgStdBn\xff\xfe\0\0\0\x03' + struct.pack('<HHI', 2, 0, 0) + bytes(10))
    for magic, sec in ((b'LBL1', lbl), (b'TXT2', txt)):
        out += magic + struct.pack('<I', len(sec)) + bytes(8) + sec
        out += b'\xab' * (-len(out) % 16)
    struct.pack_into('<I', out, 0x12, len(out))
    return bytes(out)


def tag(group, kind, params=b''):
    return b'\x0e' + struct.pack('<HHH', group, kind, len(params)) + params


class Archives(unittest.TestCase):
    def test_sarc_round_trip_and_replace(self):
        data = make_sarc([('a.msbt', b'x' * 5), ('b/c.msbt', b'y' * 40)])
        self.assertEqual(sarc.write(data, {}), data)
        new = sarc.write(data, {'a.msbt': b'z' * 100})
        self.assertEqual(dict(sarc.read(new)), {'a.msbt': b'z' * 100, 'b/c.msbt': b'y' * 40})
        with self.assertRaises(KeyError):
            sarc.write(data, {'missing': b''})

    def test_msbt_round_trip_and_new_text(self):
        msgs = [b'hello', tag(0, 3, b'\0\0') + b'red' + tag(0, 3, b'\xff\xff'), b'']
        data = make_msbt(msgs, ['A', 'B', 'C'])
        m = msbt.Msbt(data)
        self.assertEqual(m.messages, msgs)
        self.assertEqual([m.label(i) for i in range(3)], ['A', 'B', 'C'])
        self.assertEqual(m.encode(), data)
        again = msbt.Msbt(m.encode([b'longer text here', msgs[1], b'x']))
        self.assertEqual(again.messages[0], b'longer text here')


class Markup(unittest.TestCase):
    CASES = [
        tag(0, 0, struct.pack('<HH', 2, 6) + 'かみ'.encode()) + '神様'.encode(),
        tag(4, 0, struct.pack('<H', 5) + b'Think\0') + b'hm',
        tag(2, 1, struct.pack('<H', 3) + b'Abc\xff\xff'),
        tag(5, 1) + b'\n' + tag(1, 1, b'\0\0\0?'),
        ' to jump { and }'.encode(),
        b'\x0f\x03\0\0\0',
    ]

    def test_round_trip(self):
        for m in self.CASES:
            self.assertEqual(msbt.from_markup(msbt.to_markup(m)), m, msbt.to_markup(m))

    def test_forms(self):
        self.assertEqual(msbt.to_markup(self.CASES[0]), '{ruby:2:かみ}神様')
        self.assertEqual(msbt.to_markup(self.CASES[1]), "{4.0:'Think'}hm")
        self.assertEqual(msbt.to_markup(self.CASES[2]), "{2.1:'Abc'+ffff}")
        self.assertEqual(msbt.to_markup(self.CASES[4]), '{U+E040} to jump {{ and }}')

    def test_errors(self):
        for bad in ('a { b', '{9.x}', '{1.1:abc}', '{ruby:x:y}', 'nul\0'):
            with self.assertRaises(msbt.MarkupError, msg=bad):
                msbt.from_markup(bad)

    def test_codes_leave_readings_out(self):
        self.assertEqual(msbt.codes('{ruby:1:x}a{0.3:0000}b{0.3:ffff}{{c}}'), ['{0.3:0000}', '{0.3:ffff}'])


class Po(unittest.TestCase):
    def test_round_trip(self):
        entries = [po.Entry('L1', 'one\ntwo "q"', 'หนึ่ง\nสอง', comments=['ref: one\ntwo'], refs=['f.msbt#0']),
                   po.Entry('L2', 'x', '', flags=['fuzzy'], previous='old\nsource', notes=['mine'])]
        header, back = po.loads(po.dumps([('Language', 'th')], entries))
        self.assertEqual(header, [('Language', 'th')])
        self.assertEqual(back, entries)


class Textures(unittest.TestCase):
    def test_bc4_and_layout(self):
        h, w = 20, 36
        tex = {'name': 't', 'fmt': 0x1d01, 'w': w, 'h': h, 'th': 1, 'data': 64, 'size': 4096}
        data = bytes(range(256)) * 20
        rng = np.random.default_rng(1)
        mask = (rng.random((h, w)) > 0.5).astype(np.uint8) * 255
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[..., :3] = 255
        rgba[..., 3] = mask
        out = textures.encode(tex, data, rgba)
        self.assertEqual(len(out), len(data))
        self.assertTrue((textures.decode(tex, out)[..., 3] == mask).all())   # two values per block: exact
        wb, hb, bpb = textures._geometry(tex)
        lin = textures.ut.deswizzle(tex, out, wb * bpb, hb)
        self.assertEqual(textures.swizzle_into(tex, out, lin), out)


class Thai(unittest.TestCase):
    def test_clusters(self):
        text = 'ที่นี่ ปั้น น้ำ กา'
        clusters = fonts.clusters_in([text])
        self.assertEqual(clusters, sorted(['ที่', 'นี่', 'ปั้', 'น้ำ']))
        codes = fonts.cluster_codes(clusters)
        out = fonts.precompose(text, codes)
        self.assertNotIn('่', out)
        self.assertTrue(all(0xe800 <= ord(c) < 0xf700 for c in codes.values()))
        self.assertEqual(out.count(' '), 3)


if __name__ == '__main__':
    unittest.main()
