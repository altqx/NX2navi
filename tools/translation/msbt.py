"""MSBT message files, as the game stores them: UTF-8 text (header byte 0x0c is 0), sections LBL1 (labels) and TXT2
(messages), each section padded with 0xab to 16 bytes.

A message is UTF-8 text with control codes, ending in one NUL byte. A control code is 0x0e, then u16 group, u16 type,
u16 parameter size and the parameters; 0x0f, u16 group, u16 type closes one (the game's text has none, but they are
kept). Group 0 type 0 is a reading (furigana) over the next characters: u16 character count, u16 byte length, UTF-8.

The markup is what a translator edits: the text as it is, with
    {G.T}               a control code without parameters (group G, type T, decimal)
    {G.T:HEX}           one with parameters, as hex bytes
    {G.T:'name'}        one whose parameters are a u16 length, that many ASCII characters and a zero byte
    {G.T:'name'+HEX}    the same with other bytes than one zero after the name
    {/G.T}              a closing code
    {ruby:N:reading}    a reading over the next N characters
    {U+E040}            a private-use character (the game's button icons), so it cannot be lost unseen
    {{ and }}           a literal brace
"""
import re
import struct

PAD = 0xab
RUBY = (0, 0)


class MarkupError(ValueError):
    pass


class Msbt:
    def __init__(self, data):
        data = bytes(data)
        if data[:8] != b'MsgStdBn' or data[8:10] != b'\xff\xfe':
            raise ValueError('not a little-endian MSBT file')
        if data[0x0c] != 0:
            raise ValueError(f'MSBT text encoding {data[0x0c]} is not UTF-8')
        self.header = data[:0x20]
        self.sections = []           # [magic, the 16-byte section header, body]
        pos = 0x20
        for _ in range(struct.unpack_from('<H', data, 0x0e)[0]):
            size = struct.unpack_from('<I', data, pos + 4)[0]
            self.sections.append([data[pos:pos + 4], data[pos:pos + 16], data[pos + 16:pos + 16 + size]])
            pos = (pos + 16 + size + 15) & ~15
        self.labels = self._labels()
        self.messages = self._messages()

    def _section(self, magic):
        for s in self.sections:
            if s[0] == magic:
                return s
        raise ValueError(f'MSBT has no {magic.decode()} section')

    def _labels(self):
        """Message index -> label, from the hash table of LBL1."""
        body = self._section(b'LBL1')[2]
        labels = {}
        for g in range(struct.unpack_from('<I', body, 0)[0]):
            count, at = struct.unpack_from('<II', body, 4 + 8 * g)
            for _ in range(count):
                n = body[at]
                labels[struct.unpack_from('<I', body, at + 1 + n)[0]] = body[at + 1:at + 1 + n].decode('ascii')
                at += 1 + n + 4
        return labels

    def _messages(self):
        """Each message's bytes, its closing NUL left out."""
        body = self._section(b'TXT2')[2]
        count = struct.unpack_from('<I', body, 0)[0]
        offsets = list(struct.unpack_from(f'<{count}I', body, 4)) + [len(body)]
        out = []
        for i in range(count):
            m = body[offsets[i]:offsets[i + 1]]
            if not m.endswith(b'\0'):
                raise ValueError(f'message {i} does not end in NUL')
            out.append(m[:-1])
        return out

    def label(self, i):
        return self.labels.get(i, f'#{i}')

    def encode(self, messages=None):
        """The file with messages (a list of message bytes without their NUL; default: the ones read)."""
        messages = self.messages if messages is None else messages
        if len(messages) != len(self.messages):
            raise ValueError('the message count cannot change')
        table = bytearray(struct.pack('<I', len(messages)))
        text = bytearray()
        start = 4 + 4 * len(messages)
        for m in messages:
            table += struct.pack('<I', start + len(text))
            text += m + b'\0'
        out = bytearray(self.header)
        for magic, head, body in self.sections:
            if magic == b'TXT2':
                body = bytes(table + text)
            out += head[:4] + struct.pack('<I', len(body)) + head[8:16] + body
            out += bytes([PAD]) * (-len(out) % 16)
        struct.pack_into('<I', out, 0x12, len(out))
        return bytes(out)


# ---- message bytes <-> markup

def tokens(message):
    """[str | ('tag', group, type, params) | ('close', group, type)] of a message's bytes."""
    out, text, i = [], bytearray(), 0
    while i < len(message):
        c = message[i]
        if c in (0x0e, 0x0f):
            if text:
                out.append(text.decode('utf-8'))
                text = bytearray()
            if c == 0x0e:
                group, kind, size = struct.unpack_from('<HHH', message, i + 1)
                out.append(('tag', group, kind, bytes(message[i + 7:i + 7 + size])))
                i += 7 + size
            else:
                group, kind = struct.unpack_from('<HH', message, i + 1)
                out.append(('close', group, kind))
                i += 5
        else:
            text.append(c)
            i += 1
    if text:
        out.append(text.decode('utf-8'))
    return out


def _named(params):
    """(name, rest) when params are a u16 length, that many plain ASCII characters and something after; else None."""
    if len(params) < 3:
        return None
    n = struct.unpack_from('<H', params, 0)[0]
    name = params[2:2 + n]
    if n == 0 or len(name) != n or not re.fullmatch(rb"[A-Za-z0-9_.\-]+", name):
        return None
    return name.decode('ascii'), params[2 + n:]


def tag_markup(token):
    if token[0] == 'close':
        return f'{{/{token[1]}.{token[2]}}}'
    _, group, kind, params = token
    if (group, kind) == RUBY and len(params) >= 4:
        count, size = struct.unpack_from('<HH', params, 0)
        reading = params[4:]
        if size == len(reading):
            try:
                text = reading.decode('utf-8')
                if not re.search(r'[{}:]', text):
                    return f'{{ruby:{count}:{text}}}'
            except UnicodeDecodeError:
                pass
    head = f'{group}.{kind}'
    if not params:
        return f'{{{head}}}'
    named = _named(params)
    if named:
        name, rest = named
        return f"{{{head}:'{name}'}}" if rest == b'\0' else f"{{{head}:'{name}'+{rest.hex()}}}"
    return f'{{{head}:{params.hex()}}}'


def _escape_text(text):
    text = text.replace('{', '{{').replace('}', '}}')
    return re.sub('[-]', lambda m: f'{{U+{ord(m.group()):04X}}}', text)


def to_markup(message):
    return ''.join(_escape_text(t) if isinstance(t, str) else tag_markup(t) for t in tokens(message))


_TAG = re.compile(r"""\{\{|\}\}|\{([^{}]*)\}|[{}]""")
_PLAIN = re.compile(r"""(\d+)\.(\d+)(?::(?:'([A-Za-z0-9_.\-]+)'(?:\+([0-9a-fA-F]*))?|([0-9a-fA-F]+)))?""")


def _tag_bytes(inner):
    if inner.startswith('ruby:'):
        parts = inner.split(':', 2)
        if len(parts) != 3 or not parts[1].isdigit():
            raise MarkupError(f'{{{inner}}}: a reading is {{ruby:N:reading}}')
        reading = parts[2].encode('utf-8')
        params = struct.pack('<HH', int(parts[1]), len(reading)) + reading
        return b'\x0e' + struct.pack('<HHH', *RUBY, len(params)) + params
    if inner.startswith('U+'):
        try:
            return chr(int(inner[2:], 16)).encode('utf-8')
        except ValueError:
            raise MarkupError(f'{{{inner}}}: not a character') from None
    close = inner.startswith('/')
    m = _PLAIN.fullmatch(inner[1:] if close else inner)
    if not m:
        raise MarkupError(f'{{{inner}}} is not a control code')
    group, kind = int(m.group(1)), int(m.group(2))
    if group > 0xffff or kind > 0xffff:
        raise MarkupError(f'{{{inner}}}: group and type are 16-bit')
    if close:
        if m.group(3) or m.group(5):
            raise MarkupError(f'{{{inner}}}: a closing code has no parameters')
        return b'\x0f' + struct.pack('<HH', group, kind)
    if m.group(3) is not None:
        name = m.group(3).encode('ascii')
        rest = bytes.fromhex(m.group(4)) if m.group(4) is not None else b'\0'
        if m.group(4) is not None and len(m.group(4)) % 2:
            raise MarkupError(f'{{{inner}}}: odd number of hex digits')
        params = struct.pack('<H', len(name)) + name + rest
    elif m.group(5) is not None:
        if len(m.group(5)) % 2:
            raise MarkupError(f'{{{inner}}}: odd number of hex digits')
        params = bytes.fromhex(m.group(5))
    else:
        params = b''
    return b'\x0e' + struct.pack('<HHH', group, kind, len(params)) + params


def from_markup(text):
    """The message bytes of markup text; MarkupError names what cannot be read."""
    if '\0' in text:
        raise MarkupError('a NUL character inside the text')
    out = bytearray()
    pos = 0
    for m in _TAG.finditer(text):
        out += text[pos:m.start()].encode('utf-8')
        pos = m.end()
        s = m.group()
        if s == '{{':
            out += b'{'
        elif s == '}}':
            out += b'}'
        elif m.group(1) is None:
            raise MarkupError(f'a lone "{s}" at character {m.start()}: write {s}{s} for the character itself')
        else:
            out += _tag_bytes(m.group(1))
    out += text[pos:].encode('utf-8')
    return bytes(out)


def without_readings(markup):
    """The markup with its readings left out, easier to read."""
    return re.sub(r'\{ruby:\d+:[^{}]*\}', '', markup)


def codes(markup):
    """The control codes of markup text, readings left out (a translation drops or adds them freely), in order."""
    return [m.group() for m in re.finditer(r'\{[^{}]*\}', markup.replace('{{', '').replace('}}', ''))
            if not m.group().startswith(('{ruby:', '{U+'))]


def icons(markup):
    return re.findall(r'\{U\+[0-9A-Fa-f]+\}', markup)
