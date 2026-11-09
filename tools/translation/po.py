"""Gettext PO files, the part a translation project uses: a header, then entries with translator (#) and extracted (#.)
comments, references (#:), flags (#,), the previous source (#| msgid), msgctxt, msgid and msgstr. Obsolete entries
(#~) are read past. Text is UTF-8."""
from dataclasses import dataclass, field

_ESC = {'\\': '\\\\', '"': '\\"', '\n': '\\n', '\t': '\\t', '\r': '\\r'}
_UNESC = {'\\': '\\', '"': '"', 'n': '\n', 't': '\t', 'r': '\r', 'a': '\a', 'b': '\b', 'f': '\f', 'v': '\v'}


@dataclass
class Entry:
    ctx: str
    msgid: str
    msgstr: str = ''
    notes: list = field(default_factory=list)       # translator comments, "# ..."
    comments: list = field(default_factory=list)    # extracted comments, "#. ..."
    refs: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    previous: str | None = None

    @property
    def fuzzy(self):
        return 'fuzzy' in self.flags

    def set_fuzzy(self, on):
        if on and not self.fuzzy:
            self.flags.append('fuzzy')
        elif not on and self.fuzzy:
            self.flags.remove('fuzzy')


def _quote(s):
    return '"' + ''.join(_ESC.get(c, c) for c in s) + '"'


def _field(keyword, s, prefix=''):
    """keyword "text", split after each newline when the text has more than one line."""
    lines = s.split('\n')
    pieces = [line + '\n' for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])
    if len(pieces) <= 1:
        return [f'{prefix}{keyword} {_quote(s)}']
    return [f'{prefix}{keyword} ""'] + [f'{prefix}{_quote(p)}' for p in pieces]


def dumps(header, entries):
    """header: [(name, value)]."""
    out = ['msgid ""', 'msgstr ""'] + [_quote(f'{k}: {v}\n') for k, v in header]
    for e in entries:
        out.append('')
        out += [f'# {n}'.rstrip() for n in e.notes]
        for c in e.comments:
            out += [f'#. {line}'.rstrip() for line in c.split('\n')]
        out += [f'#: {r}' for r in e.refs]
        if e.flags:
            out.append('#, ' + ', '.join(e.flags))
        if e.previous is not None:
            out += _field('msgid', e.previous, '#| ')
        out += _field('msgctxt', e.ctx)
        out += _field('msgid', e.msgid)
        out += _field('msgstr', e.msgstr)
    return '\n'.join(out) + '\n'


def _unquote(s, where):
    s = s.strip()
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        raise ValueError(f'{where}: expected a quoted string, got {s[:40]!r}')
    out, i, body = [], 0, s[1:-1]
    while i < len(body):
        c = body[i]
        if c == '\\' and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt not in _UNESC:
                raise ValueError(f'{where}: unknown escape \\{nxt}')
            out.append(_UNESC[nxt])
            i += 2
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def _comment(rest):
    """A comment line's text: dumps writes one space after the marker; spaces the text itself starts with stay."""
    return (rest[1:] if rest.startswith(' ') else rest).rstrip()


def loads(text, name='<po>'):
    """(header [(name, value)], [Entry])."""
    entries, header = [], []
    cur, key, prev_key = None, None, None

    def start():
        return {'notes': [], 'comments': [], 'refs': [], 'flags': [], 'previous': None,
                'msgctxt': None, 'msgid': None, 'msgstr': None}

    def finish(e):
        if e is None or e['msgid'] is None:
            return
        if e['msgid'] == '' and e['msgctxt'] is None:
            for line in (e['msgstr'] or '').split('\n'):
                if ':' in line:
                    k, v = line.split(':', 1)
                    header.append((k.strip(), v.strip()))
            return
        entries.append(Entry(e['msgctxt'] or '', e['msgid'], e['msgstr'] or '', e['notes'], e['comments'], e['refs'],
                             e['flags'], e['previous']))

    for n, raw in enumerate(text.splitlines(), 1):
        where = f'{name}:{n}'
        line = raw.strip()
        if not line:
            continue
        if line.startswith('#~'):
            continue
        if line.startswith('#'):
            if cur is not None and cur['msgid'] is not None and cur['msgstr'] is not None:
                finish(cur)
                cur = None
            cur = cur or start()
            if line.startswith('#.'):
                cur['comments'].append(_comment(line[2:]))
            elif line.startswith('#:'):
                cur['refs'] += line[2:].split()
            elif line.startswith('#,'):
                cur['flags'] += [f.strip() for f in line[2:].split(',') if f.strip()]
            elif line.startswith('#|'):
                rest = line[2:].strip()
                if rest.startswith('msgid '):
                    cur['previous'] = _unquote(rest[6:], where)
                    prev_key = 'previous'
                elif rest.startswith('"') and prev_key == 'previous':
                    cur['previous'] += _unquote(rest, where)
            else:
                cur['notes'].append(_comment(line[1:]))
            continue
        for kw in ('msgctxt', 'msgid', 'msgstr'):
            if line.startswith(kw + ' ') or line.startswith(kw + '"'):
                if kw in ('msgctxt', 'msgid') and cur is not None and cur['msgstr'] is not None:
                    finish(cur)
                    cur = None
                cur = cur or start()
                cur[kw] = _unquote(line[len(kw):], where)
                key = kw
                break
        else:
            if line.startswith('"'):
                if cur is None or key is None:
                    raise ValueError(f'{where}: a string outside an entry')
                cur[key] += _unquote(line, where)
            else:
                raise ValueError(f'{where}: cannot read {line[:40]!r}')
    finish(cur)
    # an entry's extracted comment lines belong together: one comment of several lines
    for e in entries:
        if e.comments:
            e.comments = ['\n'.join(e.comments)]
    return header, entries
