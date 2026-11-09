"""The game's fonts: the scalable fonts of Font2/Scalable, the style files (.buif) of each Font2/Font*.bfarc that say
which font draws which characters, specimens, and Thai letters merged into a game font.

A .bfotf (CFF OpenType) or .bfttf (TrueType) is an 8-byte header and the font, every byte from offset 4 XORed with a
4-byte key; the second header word is the font's size, which gives the key away (button_icons.bfotf_decode).

A .buif style (read from the files, meaning inferred from the values): a list of entries tried in order, each naming
a font, the code point ranges it serves, a horizontal and a vertical scale and a baseline shift (in ems). The Japanese
and European archive's Normal style, for one, sends the arrows and the ellipsis to Symbol, then everything to Cetra
Display Pro at 1.17, then the Japanese fonts: a character a font lacks goes on to the next entry that covers it.
"""
import io
import re
import struct

import button_icons as bi  # tools/ is on the path of tools/translate.py

SCALABLE = 'Font2/Scalable/'
ARCHIVES = 'Font2/Font'
THAI = range(0x0e00, 0x0e80)
THAI_PUA = range(0xf700, 0xf71b)     # positioned forms of the marks some Thai fonts carry for unshaped text
ZWSP = 0x200b

# What each style is most likely used for, from its fonts and name; the game does not say.
STYLE_ROLES = {
    'Normal': 'dialogue boxes, menus and most text',
    'NormalBgWhite': 'text on light panels (heavier weight)',
    'Special': 'headings and titles (heavy display weight)',
    'StaffRoll_00': 'end credits, regular',
    'StaffRoll_01': 'end credits, medium',
    'CaptionRuby_00': 'furigana over kanji (Japanese only)',
    'Number': 'numbers (counters, HUD)',
    'NumberDeco': 'decorated numbers',
    'ZeldaGlyphPlain': 'Hylian script and game symbols',
    'ZeldaGlyphDeco': 'Hylian script and game symbols, decorated',
    'External_00': 'controller button icons',
}
ARCHIVE_LANGUAGES = {
    'Font': 'Japanese, English, French, German, Spanish, Italian, Dutch, Polish, Portuguese',
    'Font_EUru': 'Russian',
    'Font_KRko': 'Korean',
    'Font_CNzh': 'Simplified Chinese',
    'Font_TWzh': 'Traditional Chinese',
}


def decode(blob):
    """(font bytes, key) of a .bfotf or .bfttf."""
    key = bi.xor(blob[4:8], struct.pack('>I', len(blob) - 8))
    font = bi.xor(blob[8:], key)
    if font[:4] not in (b'OTTO', b'\0\1\0\0', b'true'):
        raise ValueError('not an OpenType or TrueType font after the header')
    return font, key


def encode(font, key, head):
    font = font + bytes(-len(font) % 4)
    return head[:4] + bi.xor(struct.pack('>I', len(font)) + font, key)


def font_name(path):
    return path[len(SCALABLE):].split('.')[0]


def scalable(game):
    """Font name -> RomFS path of every scalable font."""
    return {font_name(p): p for p in sorted(game.files) if p.startswith(SCALABLE)}


# ---- styles

def _u32(b, at): return struct.unpack_from('<I', b, at)[0]
def _u64(b, at): return struct.unpack_from('<Q', b, at)[0]


def _cstr(b, at):
    return b[at:b.index(0, at)].decode('utf-8')


def style_entries(data):
    """[{'font', 'ranges', 'x', 'y', 'shift'}] of a .buif, in the order tried; [] when the layout is not the one known."""
    base = data.find(b'BUIF')
    if base < 0:
        return []
    try:
        table, count = _u64(data, base + 0x10), _u32(data, base + 0x18)
        out = []
        for i in range(count):
            e = _u64(data, table + 8 * i)
            ranges_at, ranges = _u64(data, e + 8), _u32(data, e + 0x10)
            ranges = [(_u32(data, p), _u32(data, p + 4)) for p in (_u64(data, ranges_at + 8 * j) for j in range(ranges))]
            _, x, y, shift = struct.unpack_from('<4f', data, _u64(data, e + 0x18))
            out.append({'font': _cstr(data, _u64(data, e)), 'ranges': ranges, 'x': x, 'y': y, 'shift': shift})
        return out
    except (struct.error, ValueError, IndexError):
        return []


def styles(game):
    """Archive name -> style name -> entries, for every Font2/Font*.bfarc."""
    from . import sarc
    out = {}
    for path in sorted(p for p in game.files if p.startswith(ARCHIVES) and '.bfarc' in p):
        archive = path.split('/')[1].split('.')[0]
        out[archive] = {name.split('.')[0]: style_entries(data) for name, data in sarc.read(game.read(path))}
    return out


def ranges_text(ranges):
    def one(lo, hi):
        if lo == 0 and hi >= 0x10ffff:
            return 'everything'
        return f'U+{lo:04X}' if lo == hi else f'U+{lo:04X}-U+{hi:04X}'
    return ', '.join(one(lo, hi) for lo, hi in ranges)


# ---- fonts as fontTools objects

def tt(font_bytes):
    from fontTools.ttLib import TTFont
    return TTFont(io.BytesIO(font_bytes))


SCRIPTS = (('Latin', 'Aa'), ('Japanese kana', 'あア'), ('Kanji', '時'), ('Cyrillic', 'Дж'), ('Hangul', '한'),
           ('Chinese', '们們'), ('Thai', 'กไ'))


def describe(font_bytes):
    t = tt(font_bytes)
    cmap = t.getBestCmap() or {}
    name = t['name']
    return {
        'family': name.getDebugName(16) or name.getDebugName(1),
        'subfamily': name.getDebugName(17) or name.getDebugName(2),
        'format': 'CFF OpenType' if 'CFF ' in t else 'TrueType',
        'cid': 'CFF ' in t and hasattr(t['CFF '].cff.topDictIndex[0], 'ROS'),
        'upm': t['head'].unitsPerEm,
        'glyphs': len(t.getGlyphOrder()),
        'chars': len(cmap),
        'scripts': [s for s, probe in SCRIPTS if all(ord(c) in cmap for c in probe)],
        'weight': t['OS/2'].usWeightClass if 'OS/2' in t else None,
        'cmap': cmap,
    }


def _height(t, char):
    """The top of char's outline in font units, None when the font lacks it."""
    from fontTools.pens.boundsPen import BoundsPen
    cmap = t.getBestCmap() or {}
    if ord(char) not in cmap:
        return None
    gs = t.getGlyphSet()
    pen = BoundsPen(gs)
    gs[cmap[ord(char)]].draw(pen)
    return pen.bounds[3] if pen.bounds else None


def auto_scale(game_font, thai_font):
    """The scale that gives the Thai font's Latin x the game font's x height, so the Thai letters keep the size their
    designer gave them beside Latin; the em ratio when either lacks an x."""
    g, t = tt(game_font), tt(thai_font)
    gx, tx = _height(g, 'x'), _height(t, 'x')
    if gx and tx:
        return gx / tx
    return g['head'].unitsPerEm / t['head'].unitsPerEm


# Thai clusters: a letter with the marks above or below it (and a closing sara am). A text engine without Thai shaping
# draws each mark at its default place, where it collides with a tall letter (ป ฝ ฟ), with another mark (ที่) or
# with a letter's tail (ญู ฐู). The build shapes every cluster the translation uses once per font, as a full text
# shaper with the Thai font's own rules places it, draws the result as one glyph at a private-use code point and
# writes that code point into the game's text in place of the cluster.
MARKS = '\u0e31\u0e34-\u0e3a\u0e47-\u0e4e'
CLUSTER = re.compile(f'[\u0e01-\u0e30\u0e32\u0e33\u0e3f-\u0e46\u0e4f-\u0e5b][{MARKS}]+\u0e33?')
CLUSTER_CODES = range(0xe800, 0xf700)   # the game's own private-use characters (button icons) are U+E040..U+E09B


def clusters_in(texts):
    """The Thai clusters with marks of texts, sorted."""
    return sorted({c for t in texts for c in CLUSTER.findall(t)})


def cluster_codes(clusters):
    """{cluster: private-use character}, in the order given."""
    if len(clusters) > len(CLUSTER_CODES):
        raise ValueError(f'{len(clusters)} different Thai clusters; at most {len(CLUSTER_CODES)} fit')
    return {c: chr(CLUSTER_CODES[i]) for i, c in enumerate(clusters)}


def precompose(text, codes):
    """text with each cluster of codes replaced by its private-use character."""
    return CLUSTER.sub(lambda m: codes.get(m.group(), m.group()), text)


def merge_thai(game_font, thai_font, size=1.0, shift=0.0, clusters=None):
    """The game font (CFF OpenType, not CID-keyed) with the Thai letters (U+0E00..U+0E7F), the positioned mark forms
    of U+F700..U+F71A when the Thai font has them, a zero-width space, and one glyph for each cluster of clusters
    ({cluster: private-use character}, shaped with the Thai font), all from the Thai font (any outline format) at
    auto_scale times size and raised by shift ems. Characters the game font has stay its own.
    Returns (font bytes, the factor from Thai font units to game font units, the characters added)."""
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.pens.transformPen import TransformPen
    unit = auto_scale(game_font, thai_font) * size
    g, t = tt(game_font), tt(thai_font)
    if 'CFF ' not in g:
        raise ValueError('the game font is not a CFF OpenType font')
    cff = g['CFF '].cff
    top = cff.topDictIndex[0]
    if hasattr(top, 'ROS'):
        raise ValueError('the game font is CID-keyed; merge into a Latin font of the style instead (fonts.md)')
    private = top.Private
    nominal = getattr(private, 'nominalWidthX', 0)
    default = getattr(private, 'defaultWidthX', 0)
    gcmap, tcmap = g.getBestCmap(), t.getBestCmap()
    tgs = t.getGlyphSet()
    tnames = t.getGlyphOrder()
    lift = shift * g['head'].unitsPerEm
    order = list(g.getGlyphOrder())
    hmtx = g['hmtx'].metrics
    charstrings = top.CharStrings
    added = []

    def add(cp, advance, parts):
        """A glyph for cp: advance in Thai font units, parts [(Thai glyph name, x, y)] in Thai font units."""
        name = f'uni{cp:04X}.th'
        advance = round(advance * unit)
        pen = T2CharStringPen(None if advance == default else advance - nominal, tgs)   # tgs: for components
        for src, x, y in parts:
            tgs[src].draw(TransformPen(pen, (unit, 0, 0, unit, x * unit, y * unit + lift)))
        cs = pen.getCharString(private=private, globalSubrs=cff.GlobalSubrs)
        if charstrings.charStringsAreIndexed:
            charstrings.charStringsIndex.append(cs)
            charstrings.charStrings[name] = len(charstrings.charStringsIndex) - 1
        else:
            charstrings.charStrings[name] = cs
        top.charset.append(name)
        order.append(name)
        cs.calcBounds(charstrings)
        hmtx[name] = (advance, round(cs.bounds[0]) if getattr(cs, 'bounds', None) else 0)
        added.append(cp)
        for table in g['cmap'].tables:
            if table.isUnicode() and not (cp > 0xffff and table.format == 4):
                table.cmap[cp] = name

    for cp in list(THAI) + list(THAI_PUA):
        if cp in tcmap and cp not in gcmap:
            add(cp, t['hmtx'].metrics[tcmap[cp]][0], [(tcmap[cp], 0, 0)])
    if ZWSP not in gcmap:
        add(ZWSP, 0, [])
    if clusters:
        import uharfbuzz as hb
        shaper = hb.Font(hb.Face(hb.Blob(thai_font)))    # its scale is the font's units per em
        for cluster, ch in clusters.items():
            buf = hb.Buffer()
            buf.add_str(cluster)
            buf.guess_segment_properties()
            hb.shape(shaper, buf, {})
            parts, x = [], 0
            for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
                parts.append((tnames[info.codepoint], x + pos.x_offset, pos.y_offset))
                x += pos.x_advance
            add(ord(ch), x, parts)
    g.setGlyphOrder(order)
    out = io.BytesIO()
    g.save(out)
    return out.getvalue(), unit, added


def _shape(font_bytes, text, names):
    """[(glyph name, x, y)] and the advance of text shaped with the font, in its units."""
    import uharfbuzz as hb
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hb.Font(hb.Face(hb.Blob(font_bytes))), buf, {})
    parts, x = [], 0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        parts.append((names[info.codepoint], x + pos.x_offset, pos.y_offset))
        x += pos.x_advance
    return parts, x


def replace_font(game_font, new_font, size=1.0, shift=0.0, clusters=None, keep=True):
    """A font that takes the game font's place: every character of new_font (any outline format) at auto_scale times
    size, raised by shift ems, one glyph for each cluster of clusters ({cluster: private-use character}, shaped with
    new_font), a zero-width space, and (keep) the game font's own glyphs for the characters new_font lacks (kana,
    kanji, Hangul), so untranslated text still draws. CID-keyed fonts can be replaced. The result has the game font's
    outline format, units per em, vertical metrics and names, so the styles that name it and their scales still apply.
    Returns (font bytes, the factor from new font units to game font units, the characters from new_font)."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    from fontTools.pens.transformPen import TransformPen
    unit = auto_scale(game_font, new_font) * size
    g, n = tt(game_font), tt(new_font)
    cff = 'CFF ' in g
    upm = g['head'].unitsPerEm
    lift = shift * upm
    gcmap, ncmap = g.getBestCmap() or {}, n.getBestCmap() or {}
    ggs, ngs = g.getGlyphSet(), n.getGlyphSet()
    order, outlines, metrics, cmap = [], {}, {}, {}
    made = {}

    def glyph(name, advance, parts):
        """parts [(glyph set, glyph name, scale, x, y)], advance in game units."""
        rec = DecomposingRecordingPen(None)
        for gs, src, k, x, y in parts:
            dec = DecomposingRecordingPen(gs)
            gs[src].draw(dec)
            dec.replay(TransformPen(rec, (k, 0, 0, k, x, y)))
        bounds = BoundsPen(None)
        rec.replay(bounds)
        advance = max(0, round(advance))
        if cff:
            from fontTools.pens.t2CharStringPen import T2CharStringPen
            pen = T2CharStringPen(advance, None)
            rec.replay(pen)
            outlines[name] = pen.getCharString()
        else:
            from fontTools.pens.cu2quPen import Cu2QuPen
            from fontTools.pens.ttGlyphPen import TTGlyphPen
            pen = TTGlyphPen(None)
            rec.replay(Cu2QuPen(pen, 1.0, reverse_direction=False))
            outlines[name] = pen.glyph()
        metrics[name] = (advance, round(bounds.bounds[0]) if bounds.bounds else 0)
        order.append(name)

    def from_new(src):
        if src not in made:
            made[src] = f'n{len(order)}'
            glyph(made[src], n['hmtx'].metrics[src][0] * unit, [(ngs, src, unit, 0, lift)])
        return made[src]

    def from_game(src):
        key = ('game', src)
        if key not in made:
            made[key] = f'g{len(order)}'
            glyph(made[key], g['hmtx'].metrics[src][0], [(ggs, src, 1, 0, 0)])
        return made[key]

    glyph('.notdef', n['hmtx'].metrics['.notdef'][0] * unit, [(ngs, '.notdef', unit, 0, lift)])
    for cp, src in sorted(ncmap.items()):
        cmap[cp] = from_new(src)
    if keep:
        for cp, src in sorted(gcmap.items()):
            if cp not in cmap:
                cmap[cp] = from_game(src)
    if ZWSP not in cmap:
        glyph('zwsp', 0, [])
        cmap[ZWSP] = 'zwsp'
    names = n.getGlyphOrder()
    for cluster, ch in (clusters or {}).items():
        parts, advance = _shape(new_font, cluster, names)
        name = f'u{ord(ch):04X}'
        glyph(name, advance * unit, [(ngs, src, unit, x * unit, y * unit + lift) for src, x, y in parts])
        cmap[ord(ch)] = name

    fb = FontBuilder(upm, isTTF=not cff)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    ps = g['name'].getDebugName(6) or 'Font'
    if cff:
        fb.setupCFF(ps, {'FullName': g['name'].getDebugName(4) or ps}, outlines, {})
    else:
        fb.setupGlyf(outlines)
    fb.setupHorizontalMetrics(metrics)
    hh = g['hhea']
    fb.setupHorizontalHeader(ascent=hh.ascent, descent=hh.descent, lineGap=hh.lineGap)
    os2 = {}
    if 'OS/2' in g:
        o = g['OS/2']
        os2 = {k: getattr(o, k) for k in ('usWeightClass', 'usWidthClass', 'sTypoAscender', 'sTypoDescender',
                                          'sTypoLineGap', 'usWinAscent', 'usWinDescent', 'sxHeight', 'sCapHeight')
               if hasattr(o, k)}
    fb.setupOS2(fsType=0, **os2)
    fb.setupPost()
    fb.font['name'] = g['name']
    if 'head' in g:
        fb.font['head'].macStyle = g['head'].macStyle
    out = io.BytesIO()
    fb.font.save(out)
    return out.getvalue(), unit, sorted(ncmap)


# ---- specimens

THAI_SAMPLES = [
    'เป็นมนุษย์สุดประเสริฐเลิศคุณค่า กว่าบรรดาฝูงสัตว์เดรัจฉาน',
    'ปั้น ฟ้า ฝั่ง ป่า ที่ นี่ ญู ฐู ฎีกา ปู่ ฟื้น กั้น',
    'ได้รับ 50 รูปี!  ๐๑๒๓๔๕๖๗๘๙  0123456789',
]


def _clean(markup):
    """Markup without control codes, for a specimen."""
    text = re.sub(r'\{ruby:\d+:[^{}]*\}', '', markup)
    text = re.sub(r'\{[^{}]*\}', '', text.replace('{{', '\0').replace('}}', '\1'))
    return text.replace('\0', '{').replace('\1', '}')


def render(path, rows, width=1800):
    """A PNG of rows, each (font bytes or None for the label font, size, text, layout 'basic' | 'raqm')."""
    from PIL import Image, ImageDraw, ImageFont, features
    pad, y = 24, 24
    lines = []
    for font_bytes, size, text, layout in rows:
        if font_bytes is None:
            f = ImageFont.load_default(size=size)
        else:
            engine = ImageFont.Layout.RAQM if layout == 'raqm' and features.check('raqm') else ImageFont.Layout.BASIC
            f = ImageFont.truetype(io.BytesIO(font_bytes), size, layout_engine=engine)
        for para in text.split('\n'):
            line = ''
            for ch in para:
                if line and f.getlength(line + ch) > width - 2 * pad:
                    lines.append((f, line))
                    line = ''
                line += ch
            lines.append((f, line))
    heights = [max(f.size * 1.5, 1) for f, _ in lines]
    img = Image.new('RGB', (width, int(sum(heights) + 2 * pad)), 'white')
    d = ImageDraw.Draw(img)
    for (f, line), h in zip(lines, heights):
        d.text((pad, y), line, font=f, fill='black')
        y += h
    img.save(path)
