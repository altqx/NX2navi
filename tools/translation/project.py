"""A translation project: a folder outside the repository with project.toml, the text as PO files, edited textures,
fonts and extra RomFS files; and what is done with it (extract, status, check, build).

    project.toml                 the install folder, the language slot replaced, the mod's name, the fonts
    text/<group>/<file>.po       one per message file of the slot's text archive; msgctxt is the message's label
    glossary.tsv                 names of places, characters, items and enemies: source, reference, translation
    textures/original/...png     the slot's lettered textures; textures/reference/... the reference language's
    textures/edit/...png         edited copies, same path: the build writes these into the game's textures
    fonts/                       the fonts that supply the new letters
    romfs/                       any file here goes into the mod as it is, at the same RomFS path
    reference/                   fonts.md, the game's fonts, specimens and samples.md (tools/translate.py fonts)
"""
import collections
import re
import shutil
import tomllib
from pathlib import Path

from . import fonts, msbt, po, sarc, textures
from .game import Game

CONFIG = 'project.toml'
MOD_MARK = '.nx2navi-translation'
LANG_CODES = {'JPja': 'ja', 'USen': 'en-US', 'EUen': 'en-GB', 'EUde': 'de', 'EUfr': 'fr', 'EUes': 'es', 'EUit': 'it',
              'EUnl': 'nl', 'EUru': 'ru', 'EUpl': 'pl', 'USes': 'es-419', 'USfr': 'fr-CA', 'USpt': 'pt-BR',
              'CNzh': 'zh-Hans', 'TWzh': 'zh-Hant', 'KRko': 'ko'}
REF_PREFIX = 'ref: '
PLAIN_PREFIX = 'source without readings: '
GLOSSARY_FILES = ('LocationMsg/', 'ActorMsg/Nickname', 'StaticMsg/Reference')
GLOSSARY_LABEL = re.compile(r'_Name$')
THAI_MARKS = set('ัิีึืฺุู็่้๊๋์ํ๎')
CJK = re.compile(r'[぀-ヿ㐀-鿿]')

TEMPLATE = '''# Translation project (tools/translate.py in the NX2navi checkout). Paths are relative to this file.

# The install folder tools/make_game.py built the game into.
game = "{game}"
# The game language whose text this translation replaces. Play with that language:
# nx2navi.ini, [language] section: lang = {lang}
slot = "{slot}"
# Shown beside each message as a comment (another game language), or "" for none.
reference = "{reference}"
# The mod's folder under <game>/mods; build replaces it.
mod = "{mod}"
# The PO files' Language header.
language = "{language}"

[build]
# Use translations marked fuzzy (their source changed since they were written).
fuzzy = false
# Write the mod even when a message's control codes do not match its source (check lists them).
force = false
# Draw each Thai letter with its marks as one glyph shaped by the Thai font (the game's text engine does not place
# Thai marks itself). Only with [fonts].
shape = true

# The fonts that supply the new letters: each line merges the Thai letters of a font file into one of the game's
# fonts (reference/fonts.md lists them and which styles use them). size scales the letters (1.0 = the Thai font's
# own proportion to its Latin, matched to the game font's x height), shift raises them in ems (negative lowers).
# The Japanese fonts are CID-keyed and cannot take new letters; Cetra Display Pro serves every character the
# Japanese styles draw before them, so its two weights cover dialogue, menus, headings and credits.
# mode = "replace" puts the font file in the game font's place instead (any game font, CID-keyed ones too): all of its
# characters at the game font's x height, the game font's own glyphs kept for what it lacks (keep = false drops them).
[fonts]
# "CetraDisplayPro-Regular" = {{ file = "fonts/YourThaiFont-Regular.ttf", size = 1.0, shift = 0.0 }}
# "CetraDisplayPro-Medium" = {{ file = "fonts/YourThaiFont-Medium.ttf", size = 1.0, shift = 0.0 }}
'''


class ProjectError(Exception):
    pass


def init(root, game_folder, slot='JPja', reference='USen', mod='Thai', language='th'):
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    cfg = root / CONFIG
    if cfg.exists():
        raise ProjectError(f'{cfg} exists already')
    Game(game_folder)   # fails early on a folder without a built game
    for d in ('text', 'textures/edit', 'fonts', 'romfs', 'reference'):
        (root / d).mkdir(parents=True, exist_ok=True)
    cfg.write_text(TEMPLATE.format(game=Path(game_folder).expanduser().resolve(), slot=slot, reference=reference,
                                   mod=mod, language=language, lang=LANG_CODES.get(slot, slot)), encoding='utf-8')
    return Project(root)


class Project:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        cfg_path = self.root / CONFIG
        if not cfg_path.is_file():
            raise ProjectError(f'no {CONFIG} in {self.root}: give --project, or run tools/translate.py init first')
        cfg = tomllib.loads(cfg_path.read_text(encoding='utf-8'))
        self.game = Game(cfg['game'])
        self.slot = cfg.get('slot', 'JPja')
        self.reference = cfg.get('reference', '') or None
        self.mod = cfg.get('mod', 'Translation')
        self.language = cfg.get('language', 'th')
        self.build_cfg = cfg.get('build', {})
        self.font_cfg = cfg.get('fonts', {})
        self.text = self.root / 'text'

    # ---- game files

    def text_archive(self, lang):
        found = [p for p in self.game.files if p.startswith(f'Mals/{lang}.') and p.endswith('.sarc.zs')]
        if len(found) != 1:
            raise ProjectError(f'no single text archive Mals/{lang}.*.sarc.zs in the game')
        return found[0]

    def messages(self, lang):
        """{file name: Msbt} of a language's text archive."""
        return {name: msbt.Msbt(data) for name, data in sarc.read(self.game.read(self.text_archive(lang)))}

    def po_path(self, name):
        return self.text / (name[:-len('.msbt')] + '.po')

    # ---- extract

    def extract(self):
        """Write or update text/**.po from the game: new messages added, translations kept, a translation whose
        source changed marked fuzzy with the old source beside it. Returns counts."""
        source = self.messages(self.slot)
        ref = self.messages(self.reference) if self.reference else {}
        counts = collections.Counter()
        for name, m in sorted(source.items()):
            r = ref.get(name)
            ref_by_label = {r.label(i): msbt.to_markup(t) for i, t in enumerate(r.messages)} if r else {}
            entries = []
            for i, text in enumerate(m.messages):
                markup = msbt.to_markup(text)
                if not markup.strip():
                    continue          # an empty message stays empty
                label = m.label(i)
                e = po.Entry(ctx=label, msgid=markup, refs=[f'{name}#{i}'])
                comments = []
                if '{ruby:' in markup:
                    comments.append(PLAIN_PREFIX + msbt.without_readings(markup))
                if label in ref_by_label and ref_by_label[label].strip():
                    comments.append(REF_PREFIX + ref_by_label[label])
                e.comments = ['\n'.join(comments)] if comments else []
                if not any(c.isalpha() for c in fonts._clean(markup)):
                    e.msgstr = markup     # only codes, numbers and punctuation: nothing to translate
                    counts['copied'] += 1
                entries.append(e)
            path = self.po_path(name)
            if path.exists():
                _, old = po.loads(path.read_text(encoding='utf-8'), str(path))
                old = {e.ctx: e for e in old}
                for e in entries:
                    o = old.pop(e.ctx, None)
                    if o is None or not o.msgstr:
                        counts['new' if o is None else 'kept'] += 1
                        continue
                    e.notes = o.notes
                    e.msgstr, e.flags = o.msgstr, [f for f in o.flags if f != 'fuzzy']
                    if o.msgid != e.msgid:
                        e.set_fuzzy(True)
                        e.previous = o.msgid
                        counts['changed'] += 1
                    else:
                        e.set_fuzzy(o.fuzzy)
                        e.previous = o.previous if o.fuzzy else None
                        counts['kept'] += 1
                counts['dropped'] += sum(1 for o in old.values() if o.msgstr)
            else:
                counts['new'] += len(entries)
            header = [('Project-Id-Version', self.mod), ('Language', self.language),
                      ('MIME-Version', '1.0'), ('Content-Type', 'text/plain; charset=UTF-8'),
                      ('Content-Transfer-Encoding', '8bit'), ('X-Source-Language', LANG_CODES.get(self.slot, self.slot)),
                      ('X-Game-File', f'{self.text_archive(self.slot)}:{name}')]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(po.dumps(header, entries), encoding='utf-8')
            counts['files'] += 1
        return counts

    def write_glossary(self):
        """glossary.tsv: names of places, characters, items and enemies (source, reference, an empty translation
        column), written only when it does not exist yet, so the translator's column is never overwritten."""
        path = self.root / 'glossary.tsv'
        if path.exists():
            return None
        source = self.messages(self.slot)
        ref = self.messages(self.reference) if self.reference else {}
        rows, seen = [], set()
        for name, m in sorted(source.items()):
            whole = name.startswith(GLOSSARY_FILES)
            r = ref.get(name)
            rl = {r.label(i): t for i, t in enumerate(r.messages)} if r else {}
            for i, text in enumerate(m.messages):
                label = m.label(i)
                if not (whole or (name.startswith('ActorMsg/') and GLOSSARY_LABEL.search(label))):
                    continue
                src = fonts._clean(msbt.to_markup(text)).strip()
                if not src or '\n' in src or src in seen:
                    continue
                seen.add(src)
                en = fonts._clean(msbt.to_markup(rl[label])).strip().replace('\n', ' ') if label in rl else ''
                rows.append((src, en, '', f'{name[:-5]}:{label}'))
        lines = ['source\treference\ttranslation\twhere'] + ['\t'.join(r) for r in rows]
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return len(rows)

    # ---- reading the translation

    def po_files(self):
        return sorted(self.text.rglob('*.po'))

    def load(self, path):
        return po.loads(path.read_text(encoding='utf-8'), str(path))[1]

    def status(self):
        """[(group, entries, translated, fuzzy)] and the totals."""
        rows = collections.defaultdict(lambda: [0, 0, 0])
        for path in self.po_files():
            group = path.relative_to(self.text).parts[0]
            for e in self.load(path):
                r = rows[group]
                r[0] += 1
                if e.msgstr and e.fuzzy:
                    r[2] += 1
                elif e.msgstr:
                    r[1] += 1
        return sorted((g, *v) for g, v in rows.items())

    def check(self):
        """[(level, where, text)]: errors stop the build (markup that cannot be read, control codes that differ from
        the source's, readings left out of the count); warnings are hints (source letters left, long lines, icons)."""
        out = []
        for path in self.po_files():
            rel = path.relative_to(self.root)
            for e in self.load(path):
                if e.msgstr:
                    out += [(level, f'{rel} [{e.ctx}]', text) for level, text in check_entry(e)]
        return out

    # ---- build

    def build(self, log=print):
        """Write the mod into <game>/mods/<mod>: the text archive with every translated message, the fonts with the
        new letters, the edited textures and the files of romfs/. Returns the mod folder."""
        problems = [p for p in self.check() if p[0] == 'error']
        if problems and not self.build_cfg.get('force'):
            for level, where, text in problems[:30]:
                log(f'  {level}: {where}: {text}')
            raise ProjectError(f'{len(problems)} errors (tools/translate.py check lists them; force = true in '
                               f'[build] writes the mod anyway, leaving those messages in the source language)')
        bad = {(p[1]) for p in problems}
        mods = self.game.folder / 'mods'
        target = mods / self.mod
        if target.exists() and not (target / MOD_MARK).exists():
            raise ProjectError(f'{target} exists and was not written by this tool: choose another mod name')
        staging = mods / f'.{self.mod}.new'
        if staging.exists():
            shutil.rmtree(staging)
        romfs = staging / 'romfs'
        romfs.mkdir(parents=True)
        (staging / MOD_MARK).write_text(f'written by tools/translate.py from {self.root}\n', encoding='utf-8')

        def put(path, contents):
            dst = romfs / path
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(contents)

        # text: the translated messages of each file, then (with fonts to draw them) their Thai clusters
        # precomposed (fonts.CLUSTER)
        archive_path = self.text_archive(self.slot)
        archive = self.game.read(archive_path)
        use_fuzzy = self.build_cfg.get('fuzzy', False)
        chosen = {}
        for name, data in sarc.read(archive):
            path = self.po_path(name)
            if not path.exists():
                continue
            rel = path.relative_to(self.root)
            entries = {e.ctx: e for e in self.load(path)}
            m = msbt.Msbt(data)
            for i in range(len(m.messages)):
                e = entries.get(m.label(i))
                if not e or not e.msgstr or (e.fuzzy and not use_fuzzy) or e.msgstr == e.msgid:
                    continue
                if f'{rel} [{e.ctx}]' not in bad:
                    chosen[(name, i)] = e.msgstr
        shape = bool(self.font_cfg) and self.build_cfg.get('shape', True)
        codes = fonts.cluster_codes(fonts.clusters_in(chosen.values())) if shape else {}
        replaced = {}
        for name, data in sarc.read(archive):
            m = msbt.Msbt(data)
            new = list(m.messages)
            for i in range(len(new)):
                if (name, i) in chosen:
                    new[i] = msbt.from_markup(fonts.precompose(chosen[(name, i)], codes))
            if new != m.messages:
                replaced[name] = m.encode(new)
        if replaced:
            put(archive_path, self.game.store_like(archive_path, sarc.write(archive, replaced)))
        log(f'  text: {len(chosen)} translated messages in {len(replaced)} files'
            + (f'; {len(codes)} different Thai clusters drawn as one glyph each' if shape else ''))

        # fonts
        paths = fonts.scalable(self.game)
        for game_font, spec in self.font_cfg.items():
            if game_font not in paths:
                raise ProjectError(f'[fonts]: the game has no font {game_font} (reference/fonts.md lists them)')
            src = (self.root / spec['file']).expanduser()
            if not src.is_file():
                raise ProjectError(f'[fonts] {game_font}: {src} is missing')
            blob = self.game.read(paths[game_font])
            font, key = fonts.decode(blob)
            mode = spec.get('mode', 'merge')
            if mode == 'replace':
                merged, unit, added = fonts.replace_font(font, src.read_bytes(), spec.get('size', 1.0),
                                                         spec.get('shift', 0.0), codes, spec.get('keep', True))
            elif mode == 'merge':
                merged, unit, added = fonts.merge_thai(font, src.read_bytes(), spec.get('size', 1.0),
                                                       spec.get('shift', 0.0), codes)
            else:
                raise ProjectError(f'[fonts] {game_font}: mode is "merge" or "replace", not {mode!r}')
            put(paths[game_font], self.game.store_like(paths[game_font], fonts.encode(merged, key, blob)))
            verb = 'replaced by' if mode == 'replace' else '+'
            log(f'  font: {game_font} {verb} {len(added)} characters of {src.name} (x{unit:.3f})')

        # textures
        edits = self.texture_edits()
        for path, images in sorted(edits.items()):
            data = self.game.read(path)
            for tex in textures.textures(data):
                if tex['name'] in images:
                    data = textures.encode(tex, data, images[tex['name']])
            put(path, self.game.store_like(path, data))
        if edits:
            log(f'  textures: {len(edits)} files')

        # files as they are
        extra = [p for p in (self.root / 'romfs').rglob('*') if p.is_file()]
        for p in extra:
            put(p.relative_to(self.root / 'romfs').as_posix(), p.read_bytes())
        if extra:
            log(f'  romfs/: {len(extra)} files')

        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
        return target

    # ---- textures

    def lettered_textures(self):
        """RomFS paths of the slot's own textures (their names carry the slot's code)."""
        tag = f'_{self.slot}.'
        return sorted(p for p in self.game.files if tag in p and p.endswith('.bntx.zs'))

    def export_textures(self, paths, folder):
        """PNGs of the textures in paths under folder (one per file, or a folder per file of several textures).
        Returns [(path, texture, written or why not)]."""
        from PIL import Image
        out = []
        for path in paths:
            data = self.game.read(path)
            texs = textures.textures(data)
            for tex in texs:
                img = textures.decode(tex, data)
                stem = path[:-len('.bntx.zs')]
                dst = folder / (f'{stem}.png' if len(texs) == 1 else f'{stem}/{tex["name"]}.png')
                if img is None:
                    out.append((path, tex['name'], f'{textures.kind_name(tex)}: not exported (cannot be written back)'))
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(img, 'RGBA').save(dst)
                out.append((path, tex['name'], str(dst.relative_to(self.root))))
        return out

    def texture_edits(self):
        """RomFS path -> {texture name: RGBA array} from textures/edit, with the glow and shadow layers of a lettered
        title drawn from its letters when only the letters were edited (derived_layers)."""
        import numpy as np
        from PIL import Image
        edit = self.root / 'textures' / 'edit'
        found = collections.defaultdict(dict)
        for png in sorted(edit.rglob('*.png')):
            rel = png.relative_to(edit).as_posix()[:-len('.png')]
            if f'{rel}.bntx.zs' in self.game.files:
                path, name = f'{rel}.bntx.zs', None
            elif f'{Path(rel).parent.as_posix()}.bntx.zs' in self.game.files:
                path, name = f'{Path(rel).parent.as_posix()}.bntx.zs', Path(rel).name
            else:
                raise ProjectError(f'{png.relative_to(self.root)}: no game texture at that path')
            texs = textures.textures(self.game.read(path))
            if name is None:
                if len(texs) != 1:
                    raise ProjectError(f'{png.relative_to(self.root)}: {path} holds several textures; '
                                       f'name the image <file>/<texture>.png')
                name = texs[0]['name']
            found[path][name] = np.asarray(Image.open(png).convert('RGBA'))
        for path, layers in derived_layers(self.game, found, self.slot).items():
            found.setdefault(path, {}).update(layers)
        return found


def reference_text(entry):
    """The reference language's text of an entry: the extracted comment from its "ref: " line to the end (extract
    writes it last)."""
    lines = '\n'.join(entry.comments).split('\n')
    for i, line in enumerate(lines):
        if line.startswith(REF_PREFIX):
            return '\n'.join([line[len(REF_PREFIX):]] + lines[i + 1:])
    return None


DERIVED = ('_Glow', '_Sh')


def derived_layers(game, edits, slot):
    """The glow and shadow layers of a lettered title (UI/CustomTex/BossName: <name>_<slot>, <name>_Glow_<slot>,
    <name>_Sh_<slot>) for every edited letters layer whose two companions were not edited: each is the letters with
    a Gaussian blur of them over it, max(letters, gain * blur), radius and gain fitted to the game's own three
    layers of the same title (the game's layers follow that within a few levels)."""
    import numpy as np
    from PIL import Image, ImageFilter
    out = {}
    tail = f'_{slot}.bntx.zs'
    for path, layers in edits.items():
        if not path.endswith(tail) or any(path.endswith(d + tail) for d in DERIVED):
            continue
        stem = path[:-len(tail)]
        letters_new = textures.mask(next(iter(layers.values())))
        letters_old = _alpha(game, path)
        for d in DERIVED:
            companion = f'{stem}{d}{tail}'
            if companion not in game.files or companion in edits:
                continue
            target = _alpha(game, companion).astype(np.float64)
            best = None
            for radius in (6, 7, 8, 9, 10, 11, 12):
                blur = np.asarray(Image.fromarray(letters_old).filter(ImageFilter.GaussianBlur(radius)), dtype=np.float64)
                for gain in (1.0, 1.25, 1.5, 1.6, 1.75, 2.0):
                    err = np.abs(np.clip(np.maximum(letters_old, gain * blur), 0, 255) - target).mean()
                    if best is None or err < best[0]:
                        best = (err, radius, gain)
            _, radius, gain = best
            blur = np.asarray(Image.fromarray(letters_new).filter(ImageFilter.GaussianBlur(radius)), dtype=np.float64)
            alpha = np.clip(np.maximum(letters_new, gain * blur), 0, 255).astype(np.uint8)
            rgba = np.full(alpha.shape + (4,), 255, dtype=np.uint8)
            rgba[..., 3] = alpha
            (tex,) = textures.textures(game.read(companion))
            out[companion] = {tex['name']: rgba}
    return out


def _alpha(game, path):
    data = game.read(path)
    (tex,) = textures.textures(data)
    return textures.decode(tex, data)[..., 3]


def check_entry(e):
    """[(level, text)] of one translated entry (Project.check)."""
    out = []
    try:
        msbt.from_markup(e.msgstr)
    except msbt.MarkupError as err:
        return [('error', str(err))]
    want, got = collections.Counter(msbt.codes(e.msgid)), collections.Counter(msbt.codes(e.msgstr))
    if want != got:
        missing = ' '.join((want - got).elements())
        extra = ' '.join((got - want).elements())
        out.append(('error', f'control codes differ from the source: '
                    f'{"missing " + missing if missing else ""}{"; " if missing and extra else ""}'
                    f'{"extra " + extra if extra else ""}'))
    if collections.Counter(msbt.icons(e.msgid)) != collections.Counter(msbt.icons(e.msgstr)):
        out.append(('warning', 'button icons ({U+....}) differ from the source'))
    if e.msgstr != e.msgid and CJK.search(fonts._clean(e.msgstr)):
        out.append(('warning', 'Japanese characters left in the translation'))
    ref = reference_text(e)
    if ref:
        limit = max(len(line) for line in fonts._clean(ref).split('\n'))
        for line in fonts._clean(e.msgstr).split('\n'):
            width = sum(1 for c in line if c not in THAI_MARKS)
            if width > max(limit * 1.15, limit + 4):
                out.append(('warning', f'a line of {width} characters; the reference\'s longest is {limit}: '
                            'it may not fit the box'))
                break
    return out
