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
from translation import project  # noqa: E402


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

    def test_replace_font(self):
        # a CFF "game font" with x and a kana, a TrueType "new font" with x and Thai ko and a tone mark
        game = box_font({'x': (500, 500), 'kana': (1000, 800)}, {ord('x'): 'x', ord('あ'): 'kana'}, 1000, cff=True)
        new = box_font({'x': (300, 250), 'ko': (300, 250), 'tone': (0, 100)},
                       {ord('x'): 'x', 0x0e01: 'ko', 0x0e48: 'tone'}, 500, cff=False)
        codes = fonts.cluster_codes(['ก่'])
        out, unit, added = fonts.replace_font(game, new, clusters=codes)
        self.assertAlmostEqual(unit, 2.0)                   # 500 / 250: the x heights match
        t = fonts.tt(out)
        self.assertIn('CFF ', t)
        self.assertEqual(t['head'].unitsPerEm, 1000)
        cmap = t.getBestCmap()
        for cp in (ord('x'), ord('あ'), 0x0e01, 0x0e48, fonts.ZWSP, ord(codes['ก่'])):
            self.assertIn(cp, cmap)
        self.assertEqual(t['hmtx'][cmap[ord('x')]][0], 600)     # the new font's x, scaled
        self.assertEqual(t['hmtx'][cmap[ord('あ')]][0], 1000)   # the game's kana kept
        self.assertEqual(sorted(added), [ord('x'), 0x0e01, 0x0e48])
        dropped = fonts.tt(fonts.replace_font(game, new, keep=False)[0]).getBestCmap()
        self.assertNotIn(ord('あ'), dropped)


class PoComments(unittest.TestCase):
    def test_leading_space_of_a_comment_line_stays(self):
        text = ('msgid ""\nmsgstr ""\n"Language: th\\n"\n\n#. ref: first line\n#.  second starts with a space\n'
                'msgctxt "a"\nmsgid "x"\nmsgstr ""\n')
        header, entries = po.loads(text)
        self.assertEqual(entries[0].comments, ['ref: first line\n second starts with a space'])
        self.assertEqual(po.dumps(header, entries), text)


class Voice(unittest.TestCase):
    PACKETS = [bytes([0xf8]) + bytes(range(1, 40)), bytes([0xf8]) + bytes(300), bytes([0xf8, 0x55])]

    def switch_opus(self):
        data = b''.join(struct.pack('>II', len(p), 0x12345678) + p for p in self.PACKETS)
        head = struct.pack('<II', 0x80000001, 0x18) + struct.pack('<BBHIIII', 0, 1, 0, 48000, 0x20, 0, 312)
        return head + bytes(0x20 - len(head)) + struct.pack('<II', 0x80000004, len(data)) + data

    def test_ogg_of_switch_opus(self):
        from translation import voice
        out = voice.ogg(self.switch_opus())
        pages, at = [], 0
        while at < len(out):
            self.assertEqual(out[at:at + 4], b'OggS')
            flags, granule, crc, nseg = out[at + 5], struct.unpack_from('<q', out, at + 6)[0], \
                struct.unpack_from('<I', out, at + 22)[0], out[at + 26]
            lacing = out[at + 27:at + 27 + nseg]
            size = 27 + nseg + sum(lacing)
            page = bytearray(out[at:at + size])
            page[22:26] = bytes(4)
            self.assertEqual(voice.ogg_crc(bytes(page)), crc)
            body, packets, cur, pos = out[at + 27 + nseg:at + size], [], b'', 0
            for n in lacing:
                cur += body[pos:pos + n]
                pos += n
                if n < 255:
                    packets.append(cur)
                    cur = b''
            pages.append((flags, granule, packets))
            at += size
        self.assertEqual(pages[0][2][0][:8], b'OpusHead')
        self.assertEqual(struct.unpack_from('<H', pages[0][2][0], 10)[0], 312)    # pre-skip
        self.assertEqual(pages[1][2][0][:8], b'OpusTags')
        self.assertEqual([p for page in pages[2:] for p in page[2]], self.PACKETS)
        self.assertEqual(pages[-1][1], 3 * 960)        # three 20 ms packets
        self.assertTrue(pages[0][0] & 2 and pages[-1][0] & 4)

    def test_index(self):
        from translation import voice

        class Game:
            files = {'Voice/Resource/Stream/JPja_EventFlowMsg-_Dm_A_0000--Dm_A_0000_Text_000_b.bwav': 0,
                     'Voice/Resource/Stream/USen_EventFlowMsg-_Dm_A_0000--Dm_A_0000_Text_000_b.bwav': 0,
                     'Voice/Resource/Stream/EventFlowMsg-_Dm_B--Dm_B_Text_001.bwav': 0,
                     'Voice/Resource/JPja_EventFlowMsg-_Dm_A_0000.bars.zs': 0}
        idx = voice.index(Game)
        self.assertEqual(sorted(idx[('EventFlowMsg/Dm_A_0000', 'Dm_A_0000_Text_000_b')]), ['JPja', 'USen'])
        self.assertEqual(list(idx[('EventFlowMsg/Dm_B', 'Dm_B_Text_001')]), [''])


class Desk(unittest.TestCase):
    def test_save_changes_one_entry(self):
        import tempfile
        from types import SimpleNamespace
        from translation import server
        with tempfile.TemporaryDirectory() as tmp:
            text = Path(tmp) / 'text' / 'G'
            text.mkdir(parents=True)
            entries = [po.Entry(ctx='a', msgid='{0.3:0000}名{0.3:ffff}', comments=['ref: {0.3:0000}Name{0.3:ffff}']),
                       po.Entry(ctx='b', msgid='別', msgstr='อื่น')]
            (text / 'F.po').write_text(po.dumps([('Language', 'th')], entries), encoding='utf-8')
            desk = server.Desk.__new__(server.Desk)
            desk.p, desk.voices, desk._parsed = SimpleNamespace(text=Path(tmp) / 'text'), {}, {}
            desk.lock = __import__('threading').Lock()
            r = desk.save('G/F', 'a', 'ชื่อ', 'fuzzy', 'check the name')
            self.assertEqual([c['level'] for c in r['entry']['checks']], ['error'])     # the colour codes are gone
            self.assertEqual((r['done'], r['fuzzy'], r['draft'], r['total']), (1, 1, 0, 2))
            _, back = po.loads((text / 'F.po').read_text(encoding='utf-8'))
            self.assertEqual((back[0].msgstr, back[0].fuzzy, back[0].notes), ('ชื่อ', True, ['check the name']))
            self.assertEqual(back[1].msgstr, 'อื่น')
            with self.assertRaises(KeyError):
                desk.save('../outside', 'a', 'x', 'done', None)


class Check(unittest.TestCase):
    def test_lines_are_measured_per_box(self):
        ref = ['ref: Twelve chars{0.4}Twelve chars']
        fits = po.Entry(ctx='a', msgid='一{0.4}二', msgstr='ขนาดสิบสองตัว{0.4}ขนาดสิบสองตัว', comments=ref)
        self.assertEqual(project.check_entry(fits), [])     # two boxes, not one 24-character line
        long = po.Entry(ctx='b', msgid='一{0.4}二', msgstr='ยาวกว่าสิบสองตัวไปมากเลยนะ{0.4}สั้น', comments=ref)
        self.assertEqual([lvl for lvl, _ in project.check_entry(long)], ['warning'])

    def test_box_that_starts_with_a_line_break(self):
        e = po.Entry(ctx='a', msgid='一{0.4}二', msgstr='หนึ่ง{0.4}\nสอง')
        self.assertIn('page break', project.check_entry(e)[0][1])
        self.assertEqual(project.check_entry(po.Entry(ctx='b', msgid='一{0.4}二', msgstr='หนึ่ง{0.4}สอง')), [])


class Drafts(unittest.TestCase):
    def test_put_writes_drafts_and_keeps_reviewed_ones(self):
        import tempfile
        from types import SimpleNamespace
        from translation import drafts
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'text' / 'G').mkdir(parents=True)
            (root / 'glossary.tsv').write_text('source\treference\ttranslation\twhere\nリンク\tLink\tลิงก์\tx\n',
                                               encoding='utf-8')
            entries = [po.Entry(ctx='a', msgid='{4.0:\'Think\'}リンク！'), po.Entry(ctx='b', msgid='別', msgstr='อื่น'),
                       po.Entry(ctx='c', msgid='{4.0:\'Think\'}はい')]
            (root / 'text' / 'G' / 'F.po').write_text(po.dumps([('Language', 'th')], entries), encoding='utf-8')
            p = SimpleNamespace(root=root, text=root / 'text')
            shown = drafts.show(p, 'G/F', todo=True)
            self.assertEqual([r['ctx'] for r in shown], ['a', 'c'])
            self.assertEqual(shown[0]['names'], [{'ja': 'リンク', 'en': 'Link', 'th': 'ลิงก์'}])
            r = drafts.put(p, 'G/F', [{'ctx': 'a', 'th': "{4.0:'Think'}ลิงก์!"}, {'ctx': 'b', 'th': 'ทับ'},
                                      {'ctx': 'c', 'th': 'ครับ'}])
            self.assertEqual((r['written'], r['kept'], [c for c, _ in r['refused']]), (1, 1, ['c']))
            _, back = po.loads((root / 'text' / 'G' / 'F.po').read_text(encoding='utf-8'))
            self.assertEqual([(e.msgstr, project.state(e)) for e in back],
                             [("{4.0:'Think'}ลิงก์!", 'draft'), ('อื่น', 'done'), ('', '')])


def box_font(glyphs, cmap, upm, cff):
    """A font of boxes: glyphs {name: (advance, height)}."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    import io
    glyphs = {'.notdef': (upm // 2, upm // 2), **glyphs}
    fb = FontBuilder(upm, isTTF=not cff)
    fb.setupGlyphOrder(list(glyphs))
    fb.setupCharacterMap(cmap)
    outlines = {}
    for name, (adv, h) in glyphs.items():
        pen = T2CharStringPen(adv, None) if cff else TTGlyphPen(None)
        pen.moveTo((10, 0)); pen.lineTo((10, h)); pen.lineTo((max(adv, 40) - 10, h)); pen.lineTo((max(adv, 40) - 10, 0))
        pen.closePath()
        outlines[name] = pen.getCharString() if cff else pen.glyph()
    if cff:
        fb.setupCFF('Box', {}, outlines, {})
    else:
        fb.setupGlyf(outlines)
    fb.setupHorizontalMetrics({n: (a, 10) for n, (a, _) in glyphs.items()})
    fb.setupHorizontalHeader(ascent=upm * 4 // 5, descent=-upm // 5)
    fb.setupNameTable({'familyName': 'Box', 'styleName': 'Regular'})
    fb.setupOS2()
    fb.setupPost()
    out = io.BytesIO()
    fb.font.save(out)
    return out.getvalue()


if __name__ == '__main__':
    unittest.main()
