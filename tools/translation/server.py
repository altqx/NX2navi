"""The translator's desk: a local web page to read each message beside its reference, hear its voice in the source
and reference languages, write the translation and save it into the project's PO files.

    python tools/translate.py serve [--project P] [--port 8765]

Listens on 127.0.0.1 only: the page and the voices are the game's, for the translator alone. Every save reads the PO
file again, changes the one entry and writes the file back whole (po.dumps reproduces a file po.loads read, byte for
byte), so an edit made meanwhile in another editor to another entry is kept.

The API (JSON unless noted):
    GET  /api/project                  the project and every PO file with its counts
    GET  /api/file?path=G/F            a PO file's entries (path: text/G/F.po without .po)
    POST /api/check  {path, ctx, msgstr}               the checks of a draft, nothing saved
    POST /api/save   {path, ctx, msgstr, fuzzy, note}  saves one entry; returns its checks and the file's counts
    GET  /api/voice?path=G/F&ctx=L&lang=JPja           the voice (audio/ogg or audio/wav)
    GET  /api/search?q=TEXT                            entries whose source, reference or translation has TEXT
    GET  /api/glossary                 glossary.tsv rows
    POST /api/glossary {source, translation}           sets a name's translation
    GET  /font/<file>                  a font file of the project's fonts/ folder (for the Thai preview)
"""
import json
import mimetypes
import os
import threading
import urllib.parse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import msbt, po, voice
from . import project as pj

WEB = Path(__file__).resolve().parent / 'web'
AUDIO_CACHE = 64


class Desk:
    """The project's PO files and voices, as the page asks for them."""

    def __init__(self, project):
        self.p = project
        self.lock = threading.Lock()
        self.voices = voice.index(project.game)
        self.audio = OrderedDict()
        self._parsed = {}            # path -> (mtime, header, entries)

    # ---- PO files

    def po_path(self, rel):
        path = (self.p.text / f'{rel}.po').resolve()
        if self.p.text.resolve() not in path.parents or not path.is_file():
            raise KeyError(f'no PO file {rel}')
        return path

    def read(self, path):
        mtime = path.stat().st_mtime_ns
        hit = self._parsed.get(path)
        if hit and hit[0] == mtime:
            return hit[1], hit[2]
        header, entries = po.loads(path.read_text(encoding='utf-8'), str(path))
        self._parsed[path] = (mtime, header, entries)
        return header, entries

    def rel(self, path):
        return path.relative_to(self.p.text).with_suffix('').as_posix()

    def counts(self, rel, entries):
        voiced = sum(1 for e in entries if self.voices.get((rel, e.ctx)))
        done = sum(1 for e in entries if e.msgstr and not e.fuzzy)
        fuzzy = sum(1 for e in entries if e.msgstr and e.fuzzy)
        return {'total': len(entries), 'done': done, 'fuzzy': fuzzy, 'voiced': voiced}

    def project(self):
        files = []
        for path in self.p.po_files():
            rel = self.rel(path)
            files.append({'path': rel, **self.counts(rel, self.read(path)[1])})
        return {'slot': self.p.slot, 'reference': self.p.reference, 'language': self.p.language, 'mod': self.p.mod,
                'fonts': self.preview_fonts(), 'files': files}

    def preview_fonts(self):
        """{role: font file name}: the Thai fonts of the dialogue (Normal style) and heading (Special) game fonts."""
        files = {game_font: Path(spec.get('file', '')).name for game_font, spec in self.p.font_cfg.items()}
        return {role: files[f] for role, f in (('dialogue', 'CetraDisplayPro-Regular'),
                                               ('heading', 'CetraDisplayPro-Medium')) if f in files}

    def entry(self, rel, e):
        voices = sorted(self.voices.get((rel, e.ctx), {}))
        return {'ctx': e.ctx, 'msgid': e.msgid, 'msgstr': e.msgstr, 'fuzzy': e.fuzzy, 'notes': '\n'.join(e.notes),
                'plain': plain_text(e), 'ref': pj.reference_text(e), 'previous': e.previous, 'voices': voices,
                'codes': msbt.codes(e.msgid), 'checks': checks(e) if e.msgstr else []}

    def file(self, rel):
        _, entries = self.read(self.po_path(rel))
        return {'path': rel, 'entries': [self.entry(rel, e) for e in entries], **self.counts(rel, entries)}

    def save(self, rel, ctx, msgstr, fuzzy, note):
        path = self.po_path(rel)
        with self.lock:
            header, entries = po.loads(path.read_text(encoding='utf-8'), str(path))
            e = next((x for x in entries if x.ctx == ctx), None)
            if e is None:
                raise KeyError(f'{rel} has no message {ctx}')
            e.msgstr = msgstr.replace('\r\n', '\n')
            e.set_fuzzy(bool(fuzzy) and bool(e.msgstr))
            if not e.fuzzy:
                e.previous = None
            if note is not None:
                e.notes = [n for n in note.replace('\r\n', '\n').split('\n') if n.strip()]
            tmp = path.with_suffix('.po.saving')
            tmp.write_text(po.dumps(header, entries), encoding='utf-8')
            os.replace(tmp, path)
            self._parsed.pop(path, None)
        _, entries = self.read(path)
        return {'entry': self.entry(rel, e), **self.counts(rel, entries)}

    def draft_checks(self, rel, ctx, msgstr):
        _, entries = self.read(self.po_path(rel))
        e = next((x for x in entries if x.ctx == ctx), None)
        if e is None:
            raise KeyError(f'{rel} has no message {ctx}')
        draft = po.Entry(ctx=e.ctx, msgid=e.msgid, msgstr=msgstr, comments=e.comments)
        return checks(draft) if msgstr else []

    def search(self, q, limit=200):
        q = q.casefold()
        out = []
        for path in self.p.po_files():
            rel = self.rel(path)
            for e in self.read(path)[1]:
                hay = {'source': e.msgid, 'reference': pj.reference_text(e) or '', 'translation': e.msgstr,
                       'label': e.ctx}
                where = [k for k, v in hay.items() if q in v.casefold()]
                if where:
                    out.append({'path': rel, 'ctx': e.ctx, 'where': where, 'msgid': e.msgid,
                                'ref': hay['reference'], 'msgstr': e.msgstr})
                    if len(out) >= limit:
                        return out
        return out

    # ---- voices

    def voice(self, rel, ctx, lang):
        paths = self.voices.get((rel, ctx), {})
        path = paths.get(lang) or paths.get('')
        if not path:
            raise KeyError(f'no {lang} voice for {rel} {ctx}')
        if path not in self.audio:
            self.audio[path] = voice.audio(self.p.game.read(path))
            while len(self.audio) > AUDIO_CACHE:
                self.audio.popitem(last=False)
        self.audio.move_to_end(path)
        return self.audio[path]

    # ---- glossary

    def glossary_path(self):
        return self.p.root / 'glossary.tsv'

    def glossary(self):
        path = self.glossary_path()
        if not path.exists():
            return []
        lines = path.read_text(encoding='utf-8').split('\n')
        head = lines[0].split('\t')
        return [dict(zip(head, line.split('\t'))) for line in lines[1:] if line.strip()]

    def set_glossary(self, source, translation):
        path = self.glossary_path()
        with self.lock:
            lines = path.read_text(encoding='utf-8').split('\n')
            head = lines[0].split('\t')
            si, ti = head.index('source'), head.index('translation')
            for i, line in enumerate(lines[1:], 1):
                cols = line.split('\t')
                if len(cols) > max(si, ti) and cols[si] == source:
                    cols[ti] = translation.replace('\t', ' ').replace('\n', ' ').strip()
                    lines[i] = '\t'.join(cols)
                    break
            else:
                raise KeyError(f'the glossary has no name {source}')
            tmp = path.with_suffix('.tsv.saving')
            tmp.write_text('\n'.join(lines), encoding='utf-8')
            os.replace(tmp, path)


def plain_text(e):
    """The source without readings of an entry: the extracted comment from its prefix up to the reference line."""
    lines = '\n'.join(e.comments).split('\n')
    for i, line in enumerate(lines):
        if line.startswith(pj.PLAIN_PREFIX):
            rest = []
            for more in lines[i + 1:]:
                if more.startswith(pj.REF_PREFIX):
                    break
                rest.append(more)
            return '\n'.join([line[len(pj.PLAIN_PREFIX):]] + rest)
    return None


def checks(e):
    return [{'level': level, 'text': text} for level, text in pj.check_entry(e)]


class Handler(BaseHTTPRequestHandler):
    desk = None

    def log_message(self, fmt, *args):   # quiet: the terminal shows the address and errors only
        pass

    def send(self, status, body, mime='application/json; charset=utf-8', cache=False):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'max-age=3600' if cache else 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def fail(self, status, text):
        self.send(status, {'error': text})

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        d = self.desk
        try:
            if url.path == '/api/project':
                return self.send(200, d.project())
            if url.path == '/api/file':
                return self.send(200, d.file(q['path']))
            if url.path == '/api/voice':
                mime, data = d.voice(q['path'], q['ctx'], q.get('lang', d.p.slot))
                return self.send(200, data, mime, cache=True)
            if url.path == '/api/search':
                return self.send(200, d.search(q.get('q', '')) if q.get('q', '').strip() else [])
            if url.path == '/api/glossary':
                return self.send(200, d.glossary())
            if url.path.startswith('/font/'):
                name = urllib.parse.unquote(url.path[len('/font/'):])
                path = (d.p.root / 'fonts' / name).resolve()
                if path.parent != (d.p.root / 'fonts').resolve() or not path.is_file():
                    return self.fail(404, f'no font {name}')
                return self.send(200, path.read_bytes(), 'font/ttf', cache=True)
            name = 'index.html' if url.path in ('/', '/index.html') else url.path.lstrip('/')
            path = (WEB / name).resolve()
            if path.parent != WEB or not path.is_file():
                return self.fail(404, f'no page {url.path}')
            mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            return self.send(200, path.read_bytes(), mime + ('; charset=utf-8' if mime.startswith('text') else ''))
        except KeyError as e:
            return self.fail(404, str(e).strip("'\""))
        except (ValueError, OSError) as e:
            return self.fail(500, str(e))

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        d = self.desk
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            if url.path == '/api/save':
                return self.send(200, d.save(body['path'], body['ctx'], body.get('msgstr', ''), body.get('fuzzy'),
                                             body.get('note')))
            if url.path == '/api/check':
                return self.send(200, d.draft_checks(body['path'], body['ctx'], body.get('msgstr', '')))
            if url.path == '/api/glossary':
                d.set_glossary(body['source'], body.get('translation', ''))
                return self.send(200, {'ok': True})
            return self.fail(404, f'no action {url.path}')
        except KeyError as e:
            return self.fail(404, str(e).strip("'\""))
        except (ValueError, OSError) as e:
            return self.fail(500, str(e))


def serve(project, port=8765, ready=print):
    Handler.desk = Desk(project)
    httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    ready(f'translator\'s desk: http://127.0.0.1:{httpd.server_address[1]}/  (Ctrl+C stops it)')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
