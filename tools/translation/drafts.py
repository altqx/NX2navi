"""Machine drafts: read a PO file's messages as JSON lines and write drafts back, for a translator (a person or a
program) working outside the desk.

A draft is a translation with the flag "draft": the desk shows it as one to review, the build uses it unless
[build] drafts = false. Writing drafts never touches a message that has a reviewed translation (done or fuzzy), and
refuses a draft that check_entry finds an error in (control codes that differ from the source, broken markup).
"""
import json
import re
import os

from . import fonts, msbt, po
from . import project as pj


NOTE_PREFIX = 'draft: '
KATAKANA = re.compile(r'[ァ-ヺー・]+')
HIRAGANA = re.compile(r'[ぁ-ゟー〜～…！？!?、。・ 　]+')


def po_path(project, rel):
    path = (project.text / f'{rel}.po').resolve()
    if project.text.resolve() not in path.parents or not path.is_file():
        raise KeyError(f'no PO file {rel} (give the path under text/ without .po, as G/F)')
    return path


def glossary(project):
    """[(source, reference, translation)] of glossary.tsv, the longest source first."""
    path = project.root / 'glossary.tsv'
    if not path.exists():
        return []
    lines = path.read_text(encoding='utf-8').split('\n')
    head = lines[0].split('\t')
    rows = [dict(zip(head, line.split('\t'))) for line in lines[1:] if line.strip()]
    rows = [(r.get('source', ''), r.get('reference', ''), r.get('translation', '')) for r in rows]
    return sorted((r for r in rows if r[0]), key=lambda r: -len(r[0]))


def names_in(text, names):
    """The glossary rows whose source is in text, a longer name hiding the shorter ones inside it."""
    found = []
    for src, ref, tr in names:
        if len(src) < 2 or HIRAGANA.fullmatch(src):
            continue      # one-letter and all-hiragana rows (cries, menu words) are not names
        # a katakana name is a whole word: not part of a longer katakana word (ビリ in ビビリ)
        edge = r'(?<![ァ-ヺー])' + re.escape(src) + r'(?![ァ-ヺー])' if KATAKANA.fullmatch(src) else re.escape(src)
        if re.search(edge, text):
            found.append({'ja': src, 'en': ref, 'th': tr})
            text = re.sub(edge, '\0', text)
    return found


def show(project, rel, start=0, count=None, todo=False):
    """The messages of a PO file as dicts: n (its index in the file), ctx, ja (the source, with its codes), ja_plain
    (without readings, when it has some), en (the reference), state, th (the translation, when there is one),
    names (glossary rows the source has) and codes (the control codes the translation must keep)."""
    _, entries = po.loads(po_path(project, rel).read_text(encoding='utf-8'))
    names = glossary(project)
    out = []
    for n, e in enumerate(entries):
        if n < start or (todo and pj.state(e) not in ('', 'draft')):
            continue
        plain = msbt.without_readings(e.msgid)
        row = {'n': n, 'ctx': e.ctx, 'ja': e.msgid}
        if plain != e.msgid:
            row['ja_plain'] = plain
        row['en'] = pj.reference_text(e) or ''
        row['state'] = pj.state(e)
        if e.msgstr:
            row['th'] = e.msgstr
        if e.notes:
            row['notes'] = e.notes
        row['names'] = names_in(fonts._clean(plain), names)
        row['codes'] = msbt.codes(e.msgid)
        out.append(row)
        if count and len(out) >= count:
            break
    return out


def put(project, rel, rows):
    """Writes drafts: rows are {ctx, th} and an optional note (a doubt for the reviewer, added to the translator
    comments as "draft: ..."). Returns {written, kept, refused: [(ctx, why)], warnings: [(ctx, text)]}.
    kept: messages with a reviewed translation, left as they are."""
    path = po_path(project, rel)
    header, entries = po.loads(path.read_text(encoding='utf-8'), str(path))
    by_ctx = {e.ctx: e for e in entries}
    result = {'written': 0, 'kept': 0, 'refused': [], 'warnings': []}
    for row in rows:
        ctx, th = row.get('ctx'), (row.get('th') or '').replace('\r\n', '\n')
        e = by_ctx.get(ctx)
        if e is None:
            result['refused'].append((ctx, 'no message with this ctx in the file'))
            continue
        if pj.state(e) in ('done', 'fuzzy'):
            result['kept'] += 1
            continue
        if not th.strip():
            result['refused'].append((ctx, 'empty translation'))
            continue
        draft = po.Entry(ctx=e.ctx, msgid=e.msgid, msgstr=th, comments=e.comments)
        problems = pj.check_entry(draft)
        errors = [t for level, t in problems if level == 'error']
        if errors:
            result['refused'].append((ctx, '; '.join(errors)))
            continue
        result['warnings'] += [(ctx, t) for _, t in problems]
        e.msgstr = th
        e.set_draft(True)
        e.notes = [n for n in e.notes if not n.startswith(NOTE_PREFIX)]
        note = ' '.join((row.get('note') or '').split())
        if note:
            e.notes.append(NOTE_PREFIX + note)
        result['written'] += 1
    if result['written']:
        tmp = path.with_suffix('.po.saving')
        tmp.write_text(po.dumps(header, entries), encoding='utf-8')
        os.replace(tmp, path)
    return result


def read_rows(stream):
    """{ctx, th, note} rows from JSON lines (blank lines skipped) or one JSON array."""
    text = stream.read().strip()
    if text.startswith('['):
        return json.loads(text)
    return [json.loads(line) for line in text.split('\n') if line.strip()]
