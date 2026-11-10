"""Fan translation of the game's text, fonts and lettered textures, built into a player mod.

    python tools/translate.py init <project> --game <install folder> [--slot JPja] [--reference USen] [--mod Thai]
    python tools/translate.py extract   [--project P]   PO files from the game, translations kept
    python tools/translate.py status    [--project P]   progress
    python tools/translate.py check     [--project P]   errors and hints in the translation
    python tools/translate.py fonts     [--project P] [--compare FONT [--game-font NAME] [--size S] [--shift EM] [--replace]]
    python tools/translate.py textures  [--project P] [--match REGEX]   PNGs of the lettered (or matching) textures
    python tools/translate.py build     [--project P]   the mod, into <install folder>/mods/<mod>
    python tools/translate.py serve     [--project P] [--port N]   the translator's desk, a local web page: each
                                                               message with its reference and voices, saved to PO
    python tools/translate.py draft show --file G/F [--from N] [--count N] [--todo]   messages as JSON lines
    python tools/translate.py draft put --file G/F < drafts.jsonl   writes {"ctx", "th", "note"} lines as drafts (flag
                                                               "draft"), never over a reviewed translation
    python tools/translate.py selftest  [--game <install folder>]   every text archive, font and lettered texture
                                                               read and written back unchanged

The project folder (tools/translation/project.py lists its layout) lives outside the repository: it holds text and
images of the game. --project defaults to the current folder. The install folder is one tools/make_game.py built.
Needs Python 3.14 (compression.zstd), numpy, fontTools and Pillow (.venv-py314).
"""
import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from translation import fonts, msbt, reference, sarc, textures  # noqa: E402
from translation import project as pj  # noqa: E402
from translation.game import Game  # noqa: E402


def cmd_init(a):
    p = pj.init(a.folder, a.game, a.slot, a.reference, a.mod, a.language)
    print(f'project: {p.root}')
    run_extract(p)
    n = p.write_glossary()
    print(f'  glossary.tsv: {n} names')
    lines = run_textures(p, None)
    reference.write(p)
    reference.write_readme(p, lines)
    print('  README.md')


def run_extract(p):
    c = p.extract()
    print(f'  text: {c["files"]} PO files; {c["new"]} new messages, {c["kept"]} kept, {c["changed"]} changed '
          f'(marked fuzzy), {c["copied"]} without words copied as they are, {c["dropped"]} translations dropped '
          f'(their message is gone)')


def run_textures(p, match):
    paths = p.lettered_textures() if not match else sorted(
        x for x in p.game.files if x.endswith('.bntx.zs') and re.search(match, x))
    rows = p.export_textures(paths, p.root / 'textures' / 'original')
    if p.reference and not match:
        ref = [x.replace(f'_{p.slot}.', f'_{p.reference}.') for x in paths]
        p.export_textures([x for x in ref if x in p.game.files], p.root / 'textures' / 'reference')
    groups = sorted({str(Path(path).parent) for path, _, _ in rows})
    skipped = [r for r in rows if r[2].endswith('(cannot be written back)')]
    note = f'; {len(skipped)} in formats this cannot write back, left out' if skipped else ''
    print(f'  textures/original: {len(rows) - len(skipped)} images from {len(groups)} folders{note}')
    names = sorted({re.sub(r'(_Glow|_Sh)?_' + p.slot + r'$', '', Path(path).name[:-len('.bntx.zs')])
                    for path, _, _ in rows})
    return '\n'.join(f'- `{g}/`: ' + ', '.join(n for n in names) for g in groups) + '\n' if rows else '(none)\n'


def cmd_extract(a):
    run_extract(pj.Project(a.project))


def cmd_status(a):
    p = pj.Project(a.project)
    rows = p.status()
    total = [sum(r[i] for r in rows) for i in (1, 2, 3, 4)]
    print(f'{"group":24} {"messages":>9} {"done":>9} {"fuzzy":>7} {"draft":>7}')
    for g, n, done, fuzzy, draft in rows + [('total', *total)]:
        pct = f'{100 * done / n:5.1f}%' if n else ''
        print(f'{g:24} {n:9} {done:9} {fuzzy:7} {draft:7}  {pct}')


def cmd_check(a):
    p = pj.Project(a.project)
    out = p.check()
    for level, where, text in out:
        print(f'{level}: {where}: {text}')
    errors = sum(1 for o in out if o[0] == 'error')
    print(f'{errors} errors, {len(out) - errors} warnings')
    return 1 if errors else 0


def cmd_fonts(a):
    p = pj.Project(a.project)
    if a.compare:
        path, unit = reference.compare(p, Path(a.compare).expanduser(), a.game_font, a.size, a.shift, a.replace)
        print(f'{path} (Thai letters at x{unit:.3f} of their own size)')
        return 0
    reference.write(p)
    return 0


def cmd_textures(a):
    run_textures(pj.Project(a.project), a.match)


def cmd_serve(a):
    from translation import server
    server.serve(pj.Project(a.project), a.port)


def cmd_draft(a):
    import json
    from translation import drafts
    p = pj.Project(a.project)
    if a.action == 'show':
        for row in drafts.show(p, a.file, a.start, a.count, a.todo):
            print(json.dumps(row, ensure_ascii=False))
        return 0
    r = drafts.put(p, a.file, drafts.read_rows(sys.stdin))
    for ctx, why in r['refused']:
        print(f'refused: {ctx}: {why}')
    for ctx, text in r['warnings']:
        print(f'warning: {ctx}: {text}')
    print(f'{r["written"]} drafts written, {len(r["refused"])} refused, {r["kept"]} reviewed translations kept')
    return 1 if r['refused'] else 0


def cmd_build(a):
    p = pj.Project(a.project)
    t0 = time.time()
    target = p.build()
    print(f'mod: {target} ({time.time() - t0:.0f} s). Play with [language] lang = {pj.LANG_CODES.get(p.slot, p.slot)} '
          f'in nx2navi.ini.')


def cmd_selftest(a):
    import numpy as np
    g = Game(a.game)
    n = 0
    for path in sorted(x for x in g.files if x.startswith('Mals/')):
        data = g.read(path)
        members = sarc.read(data)
        rewritten = {}
        for name, m in members:
            x = msbt.Msbt(m)
            for msg in x.messages:
                if msbt.from_markup(msbt.to_markup(msg)) != msg:
                    sys.exit(f'{path} {name}: a message does not survive its markup')
                n += 1
            rewritten[name] = x.encode([msbt.from_markup(msbt.to_markup(msg)) for msg in x.messages])
        if sarc.write(data, rewritten) != data:
            sys.exit(f'{path}: not rebuilt byte for byte')
    print(f'text: {n} messages through markup and back, every archive rebuilt byte for byte')
    for path in sorted(x for x in g.files if x.startswith('Font2/')):
        blob = g.read(path)
        if '.bfarc' in path:
            ok = sarc.write(blob, {}) == blob
        else:
            font, key = fonts.decode(blob)
            ok = fonts.encode(font, key, blob) == blob
        if not ok:
            sys.exit(f'{path}: not rebuilt byte for byte')
    print('fonts: every font and style archive rebuilt byte for byte')
    count, worst = 0, 0
    for path in sorted(x for x in g.files if re.search(r'_[A-Z]{2}[a-z]{2}\.bntx\.zs$', x)):
        data = g.read(path)
        for tex in textures.textures(data):
            img = textures.decode(tex, data)
            if img is None:
                continue
            wb, hb, bpb = textures._geometry(tex)
            lin = textures.ut.deswizzle(tex, data, wb * bpb, hb)
            if textures.swizzle_into(tex, data, lin) != data:
                sys.exit(f'{path} {tex["name"]}: the block layout does not invert')
            back = textures.decode(tex, textures.encode(tex, data, img))
            worst = max(worst, int(np.abs(back.astype(int) - img.astype(int)).max()))
            count += 1
    print(f'textures: {count} lettered textures; layout inverts exactly, re-encoding is within {worst} of 255')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('init', help='a new project: text, glossary, textures, fonts')
    s.add_argument('folder')
    s.add_argument('--game', required=True, help='the install folder tools/make_game.py built')
    s.add_argument('--slot', default='JPja', help='the game language to replace (default JPja)')
    s.add_argument('--reference', default='USen', help='the language shown beside each message (default USen)')
    s.add_argument('--mod', default='Thai', help='the mod folder\'s name (default Thai)')
    s.add_argument('--language', default='th', help='the PO files\' language (default th)')
    for name in ('extract', 'status', 'check', 'fonts', 'textures', 'build', 'serve'):
        s = sub.add_parser(name)
        s.add_argument('--project', default='.', help='the project folder (default: the current folder)')
        if name == 'fonts':
            s.add_argument('--compare', metavar='FONT', help='draw this Thai font merged into a game font')
            s.add_argument('--game-font', default='CetraDisplayPro-Regular')
            s.add_argument('--size', type=float, default=1.0)
            s.add_argument('--shift', type=float, default=0.0)
            s.add_argument('--replace', action='store_true', help="put the font in the game font's place instead of merging its Thai letters")
        if name == 'serve':
            s.add_argument('--port', type=int, default=8765, help='the local port (default 8765)')
        if name == 'textures':
            s.add_argument('--match', help='a regular expression over RomFS paths: export those textures instead')
    s = sub.add_parser('draft', help='machine drafts: read messages as JSON lines, write drafts back')
    s.add_argument('action', choices=('show', 'put'))
    s.add_argument('--file', required=True, help='a PO file under text/, without .po (G/F)')
    s.add_argument('--project', default='.', help='the project folder (default: the current folder)')
    s.add_argument('--from', dest='start', type=int, default=0, help='show: from this message index')
    s.add_argument('--count', type=int, help='show: at most this many messages')
    s.add_argument('--todo', action='store_true', help='show: only messages without a reviewed translation')
    s = sub.add_parser('selftest')
    s.add_argument('--game', required=True)
    a = ap.parse_args()
    try:
        return globals()[f'cmd_{a.cmd}'](a) or 0
    except (pj.ProjectError, FileNotFoundError, KeyError, ValueError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
