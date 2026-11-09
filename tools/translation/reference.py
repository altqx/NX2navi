"""reference/ of a translation project: every game font (decoded, to look at), a specimen image of each with the game's
own text, fonts.md (which style draws what with which font) and samples.md (example texts by kind); and the project's
README.md. Everything written is derived from the game files and stays in the project folder."""
import collections

from . import fonts, msbt

MIXED = 'Link: ลิงก์ ได้รับ Kokiri Sword ดาบโคคิริ! น้ำ ปั้น'
ARCHIVE_OF_SLOT = {'EUru': 'Font_EUru', 'KRko': 'Font_KRko', 'CNzh': 'Font_CNzh', 'TWzh': 'Font_TWzh'}
SPECIMEN_LANGS = ('JPja', 'USen', 'EUru', 'KRko', 'CNzh', 'TWzh')
SAMPLE_KINDS = (
    ('Dialogue', lambda name, label: name.startswith('EventFlowMsg/'), 5),
    ('Menus and prompts', lambda name, label: name.startswith('LayoutMsg/'), 10),
    ('Place names', lambda name, label: name.startswith('LocationMsg/'), 10),
    ('Item names', lambda name, label: name == 'ActorMsg/PouchContent.msbt' and label.endswith('_Name'), 10),
    ('Item descriptions', lambda name, label: name == 'ActorMsg/PouchContent.msbt' and label.endswith('_Caption'), 3),
    ('Character names', lambda name, label: name == 'ActorMsg/Npc.msbt' and label.endswith('_Name'), 8),
    ('Enemy names', lambda name, label: name == 'ActorMsg/Enemy.msbt' and label.endswith('_Name'), 8),
)


def samples(project, lang):
    """{kind: [(label, text)]} of a language, the same labels in every language."""
    ms = project.messages(lang)
    out = collections.defaultdict(list)
    for kind, wanted, n in SAMPLE_KINDS:
        for name in sorted(ms):
            m = ms[name]
            for i, t in enumerate(m.messages):
                if len(out[kind]) >= n:
                    break
                label = m.label(i)
                text = fonts._clean(msbt.to_markup(t)).strip()
                if wanted(name, label) and sum(c.isalpha() for c in text) >= 2 and len(text) < 160:
                    out[kind].append((f'{name[:-5]}:{label}', text))
    return out


def _covered(cmap, text):
    chars = [c for c in text if not c.isspace()]
    return chars and sum(ord(c) in cmap for c in chars) >= 0.97 * len(chars)


def write(project, log=print):
    ref = project.root / 'reference'
    (ref / 'fonts').mkdir(parents=True, exist_ok=True)
    (ref / 'specimens').mkdir(parents=True, exist_ok=True)
    game = project.game
    info = {}
    for name, path in fonts.scalable(game).items():
        font, _ = fonts.decode(game.read(path))
        ext = '.otf' if font[:4] == b'OTTO' else '.ttf'
        (ref / 'fonts' / (name + ext)).write_bytes(font)
        info[name] = dict(fonts.describe(font), path=path, data=font, file=f'fonts/{name}{ext}')
    styles = fonts.styles(game)
    used = collections.defaultdict(list)
    for archive, ss in styles.items():
        for style, entries in ss.items():
            for e in entries:
                if f'{archive}:{style}' not in used[e['font']]:
                    used[e['font']].append(f'{archive}:{style}')

    langs = [lang for lang in SPECIMEN_LANGS if any(p.startswith(f'Mals/{lang}.') for p in game.files)]
    texts = {lang: samples(project, lang) for lang in langs}
    for name, d in info.items():
        rows = [(None, 30, f'{name}  -  {d["family"]} {d["subfamily"]}  -  {d["format"]}, {d["glyphs"]} glyphs  -  '
                 f'styles: {", ".join(used.get(name, [])) or "none"}', 'basic')]
        shown = False
        for lang in langs:
            lines = [t for kind in ('Place names', 'Dialogue', 'Item names') for _, t in texts[lang][kind][:3]]
            lines = [t for t in lines if _covered(d['cmap'], t)]
            if lines:
                rows.append((None, 22, lang, 'basic'))
                rows += [(d['data'], 46, t, 'basic') for t in lines[:6]]
                shown = True
        if not shown:
            chars = ''.join(chr(c) for c in sorted(d['cmap']) if c > 0x20)[:160]
            rows.append((d['data'], 54, ' '.join(chars[i:i + 20] for i in range(0, len(chars), 20)), 'basic'))
        fonts.render(ref / 'specimens' / f'{name}.png', rows)
    log(f'  reference/fonts: {len(info)} fonts, reference/specimens: {len(info)} images')

    (ref / 'fonts.md').write_text(fonts_md(project, info, styles, used), encoding='utf-8')
    (ref / 'samples.md').write_text(samples_md(project, texts), encoding='utf-8')
    log('  reference/fonts.md, reference/samples.md')


def fonts_md(project, info, styles, used):
    archive = ARCHIVE_OF_SLOT.get(project.slot, 'Font')
    out = [f'# The game\'s fonts', '',
           f'The text of `{project.slot}` is drawn with the styles of `{archive}` '
           f'({fonts.ARCHIVE_LANGUAGES.get(archive, "")}). A style is a list of entries tried in order: each names a '
           'font, the characters it serves and a size (x and y scale, baseline shift in ems). A character a font '
           'lacks goes on to the next entry that covers it, so new letters merged into an entry\'s font are drawn '
           'by that entry. The "likely use" column is inferred from the fonts and names; the game does not say.', '',
           f'## Styles of {archive}', '', '| style | likely use | entries, in order |', '|---|---|---|']
    for style, entries in sorted(styles.get(archive, {}).items()):
        chain = '<br>'.join(f'{e["font"]}: {fonts.ranges_text(e["ranges"])} (x {e["x"]:.2f}, y {e["y"]:.2f}'
                            f'{f", shift {e["shift"]:+.2f}" if e["shift"] else ""})' for e in entries) or '(not read)'
        out.append(f'| {style} | {fonts.STYLE_ROLES.get(style, "")} | {chain} |')
    out += ['', '## Every font', '',
            '| font | family | format | glyphs | scripts | used by (archive:style) | file, specimen |',
            '|---|---|---|---|---|---|---|']
    for name, d in sorted(info.items()):
        kind = d['format'] + (' (CID-keyed: cannot take new letters)' if d['cid'] else '')
        out.append(f'| {name} | {d["family"]} {d["subfamily"]} | {kind} | {d["glyphs"]} | '
                   f'{", ".join(d["scripts"]) or "symbols"} | {", ".join(used.get(name, [])) or "-"} | '
                   f'[{d["file"]}]({d["file"]}), [specimen](specimens/{name}.png) |')
    out += ['', '## Thai letters', '',
            'The Japanese fonts are CID-keyed and cannot take new letters. Cetra Display Pro sits before them in '
            'every text style and serves every character it has, so Thai letters merged into its two weights are '
            'drawn by Normal, StaffRoll_00 (Regular) and NormalBgWhite, Special, StaffRoll_01 (Medium). Map a '
            'regular Thai weight onto CetraDisplayPro-Regular and a medium or semibold one onto '
            'CetraDisplayPro-Medium in project.toml [fonts]. Special (headings) shares Cetra Medium, so it gets the '
            'same Thai weight as NormalBgWhite.', '',
            '`tools/translate.py fonts --compare <thai font>` draws a candidate merged into Cetra, as the game would '
            'get it, beside the game\'s own text.', '',
            '## The other archives', '']
    for other, ss in sorted(styles.items()):
        if other == archive:
            continue
        normal = ss.get('Normal', [])
        out.append(f'- {other} ({fonts.ARCHIVE_LANGUAGES.get(other, "")}): Normal = '
                   + ', '.join(e['font'] for e in normal))
    return '\n'.join(out) + '\n'


def samples_md(project, texts):
    out = ['# Example texts', '',
           'Real texts of the game by kind, the same message in each language, to try Thai fonts on text like the '
           'game\'s. Control codes are left out.', '']
    langs = [project.slot] + [lang for lang in (project.reference,) if lang and lang in texts]
    for kind, _, _ in SAMPLE_KINDS:
        out += [f'## {kind}', '']
        rows = {lang: dict(texts[lang][kind]) for lang in langs if lang in texts}
        for where, text in texts[project.slot][kind]:
            out.append(f'- `{where}`')
            for lang in langs:
                t = rows.get(lang, {}).get(where)
                if t:
                    out.append(f'  - {lang}: ' + t.replace('\n', ' / '))
        out.append('')
    return '\n'.join(out) + '\n'


def compare(project, thai_path, game_font='CetraDisplayPro-Regular', size=1.0, shift=0.0, replace=False):
    """reference/specimens/compare-<font>.png: the candidate merged into game_font (or replacing it: replace) as the
    build would do it, drawn
    without shaping (each character at its own place, as a plain text engine draws) and with it, beside the game's
    own text in the same font. Returns the image's path and the scale used."""
    paths = fonts.scalable(project.game)
    if game_font not in paths:
        raise KeyError(f'the game has no font {game_font}')
    font, _ = fonts.decode(project.game.read(paths[game_font]))
    thai = thai_path.read_bytes()
    codes = fonts.cluster_codes(fonts.clusters_in(fonts.THAI_SAMPLES + [MIXED]))
    make = fonts.replace_font if replace else fonts.merge_thai
    merged, unit, added = make(font, thai, size, shift, codes)
    lines = [t for _, t in samples(project, project.reference or project.slot)['Dialogue'][:2]]
    rows = [(None, 30, f'{thai_path.name} {"replacing" if replace else "merged into"} {game_font} (x{unit:.3f}, {len(added)} characters)', 'basic'),
            (None, 22, 'the game\'s own text in this font', 'basic')]
    rows += [(font, 46, t, 'basic') for t in lines]
    rows += [(None, 22, 'as the game draws it: each Thai cluster precomposed by the build (fonts.CLUSTER)', 'basic')]
    rows += [(merged, 46, fonts.precompose(t, codes), 'basic') for t in fonts.THAI_SAMPLES + [MIXED]]
    rows += [(None, 22, 'without precomposing: each mark at its default place, as a text engine without Thai shaping '
              'draws it (shape = false)', 'basic')]
    rows += [(merged, 46, t, 'basic') for t in fonts.THAI_SAMPLES]
    if replace:
        rows += [(None, 22, 'the game\'s own text in the replacement', 'basic')]
        rows += [(merged, 46, t, 'basic') for t in lines]
    out = project.root / 'reference' / 'specimens' / f'compare-{thai_path.stem}-{game_font}.png'
    out.parent.mkdir(parents=True, exist_ok=True)
    fonts.render(out, rows)
    return out, unit


README = '''# {mod} translation

A fan translation of the game's {slot} text, built into a mod for NX2navi by `tools/translate.py` (in the NX2navi
checkout). Private: this folder holds text and images taken from the game; never share it.

## The loop

1. Translate `text/**/*.po` with a PO editor (any gettext editor, or a text editor). One PO file per message file of
   the game; the context of an entry is the message's label. The comment `ref:` shows the {reference} text of the
   same message. Names: `glossary.tsv` (fill its translation column first and keep names consistent).
2. `tools/translate.py check --project .` lists errors (the build refuses them) and hints.
3. `tools/translate.py build --project .` writes the mod into `<game>/mods/{mod}`. Start the game with the {slot}
   language: in `nx2navi.ini`, section `[language]`, `lang = {lang}`. Untranslated messages stay in the source
   language.
4. `tools/translate.py status --project .` shows the progress.

After a game or tool update, `tools/translate.py extract --project .` brings the PO files up to date: translations
stay, and a message whose source changed is marked fuzzy with the old source beside it (`#| msgid`). Fuzzy
translations are left out of the build until the fuzzy mark is removed (or `fuzzy = true` in project.toml).

## Markup

The text is as the game has it, with its control codes in braces. Keep every control code of the source in the
translation, each as many times; move them to where they belong in the Thai sentence. `check` compares them.

| markup | what it is |
|---|---|
| `{{ruby:N:reading}}` | a reading (furigana) over the next N characters. Thai has none: leave them out |
| `{{0.3:0000}}` ... `{{0.3:ffff}}` | likely a colour on ... back to the normal colour: keep them around the same words |
| `{{0.2:5000}}` | likely a text size (hex 50 = 80%, 7d = 125%, 64 = 100%) |
| `{{2.N:'Name'...}}` | a value the game inserts (an item name, a number, a price) |
| `{{4.0:'Think'}}` | likely the speaker's expression or animation at that point |
| `{{1.1:...}}` | likely a pause (the parameter is a 32-bit float, seconds) |
| `{{5.0}}`, `{{5.1}}`, `{{5.2}}` | likely the text box's pacing: wait for a button, next box. Keep them where the source has them |
| `{{U+E040}}` | a button icon of the game's icon font |
| `{{{{` and `}}}}` | a literal brace |
| other `{{G.T:...}}` | other control codes: copy them as they are |

A line break in the text is a line break in the game: the source text breaks its lines by hand, so break the Thai
lines by hand as well (`check` warns about a line much longer than the reference's longest). Whether the game
wraps Thai by itself is one of the things to test early.

## Thai letters

The game's fonts have no Thai, and its text engine does not place Thai vowel and tone marks. The build handles both:
it merges the Thai letters of the fonts named in `project.toml` `[fonts]` into the game's fonts
(`reference/fonts.md` explains which; put the font files in `fonts/`), and it draws every Thai cluster the
translation uses (a letter with its marks: ที่, ปั้น, น้ำ) as one glyph, placed by the Thai font's own rules, at a
private-use code point it writes into the game's text. The PO files keep ordinary Thai.

Try a font first: `tools/translate.py fonts --compare fonts/<file>.ttf --project .` writes
`reference/specimens/compare-*.png`, the candidate merged into the game's font as the game will draw it.
(`--game-font CetraDisplayPro-Medium` for the heavier weight; `--size` and `--shift` as in `[fonts]`.)

Test early, with a few translated lines: where the game breaks a Thai line (Thai has no spaces between words; break
lines by hand as the source does), and whether the Thai size and baseline sit well beside the game's Latin.

## Textures

The game's own lettered textures for {slot} are in `textures/original` (the {reference} ones in `textures/reference`):
{textures}
To change one, copy it to the same path under `textures/edit/` and draw the Thai letters: white on transparency, as
exported. The boss titles have three layers (letters, `_Glow`, `_Sh`); when only the letters layer is in
`textures/edit/`, the build draws the other two from it with the blur the game's own layers have.

`romfs/`: any other file of the game to replace, at its RomFS path (for example an edited layout); it goes into the
mod as it is.
'''


def write_readme(project, texture_lines):
    path = project.root / 'README.md'
    from .project import LANG_CODES
    path.write_text(README.format(mod=project.mod, slot=project.slot, reference=project.reference or 'reference',
                                  lang=LANG_CODES.get(project.slot, project.slot),
                                  textures=texture_lines), encoding='utf-8')
