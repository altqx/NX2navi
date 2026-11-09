"""The voices of the messages: one stream per voiced message and language,
Voice/Resource/Stream/<lang>_<group>-_<file>--<label>.bwav, where <group>/<file> is the message file (without .msbt)
and <label> the message's label. A few streams have no language prefix; they serve every language.

A stream is a BWAV (runtime/menu_sound.c describes the header) whose channels hold PCM16 (codec 0), DSP-ADPCM (1) or
Opus (2). Opus channels point at a Switch Opus block: a header chunk (0x80000001: size, version, channel count, frame
size, sample rate, data offset, a context offset, the pre-skip) and a data chunk (0x80000004: size) of packets, each
a big-endian length, the encoder's final range, then the Opus packet. ogg() puts those packets into an Ogg Opus file,
which a browser plays as it is; PCM16 and DSP-ADPCM become a WAV file.
"""
import re
import struct

STREAMS = 'Voice/Resource/Stream/'
_NAME = re.compile(r'^(?:(?P<lang>[A-Z]{2}[a-z]{2})_)?(?P<file>[A-Za-z]+-_.+?)--(?P<label>.+)\.bwav$')


def index(game):
    """{(file without .msbt, label): {language or '': RomFS path}} of every voice stream."""
    out = {}
    for path in game.files:
        if not path.startswith(STREAMS):
            continue
        m = _NAME.match(path[len(STREAMS):])
        if not m:
            continue
        name = m['file'].replace('-_', '/', 1)
        out.setdefault((name, m['label']), {})[m['lang'] or ''] = path
    return out


# ---- BWAV

def channels(bwav):
    """[(codec, sample rate, sample count, samples offset, DSP coefficients)] of a BWAV."""
    if bwav[:4] != b'BWAV':
        raise ValueError('not a BWAV')
    count = struct.unpack_from('<H', bwav, 0x0e)[0]
    out = []
    for c in range(count):
        at = 0x10 + 0x4c * c
        codec, _pan, rate, frames = struct.unpack_from('<HHII', bwav, at)
        coefs = struct.unpack_from('<16h', bwav, at + 0x10)
        offset = struct.unpack_from('<I', bwav, at + 0x30)[0]
        out.append((codec, rate, frames, offset, coefs))
    return out


def audio(bwav):
    """(MIME type, file bytes) a browser plays: Ogg Opus for Opus streams, WAV for the others."""
    chans = channels(bwav)
    if not chans:
        raise ValueError('a BWAV without channels')
    codec = chans[0][0]
    if codec == 2:
        return 'audio/ogg', ogg(bwav[chans[0][3]:])
    if codec in (0, 1):
        return 'audio/wav', wav(bwav, chans)
    raise ValueError(f'BWAV codec {codec} is not known')


# ---- Switch Opus -> Ogg Opus

def switch_opus(block):
    """(channel count, sample rate, pre-skip, [packet]) of a Switch Opus block."""
    kind, size = struct.unpack_from('<II', block, 0)
    if kind != 0x80000001:
        raise ValueError('not a Switch Opus block')
    _version, chans, _frame, rate, data_at, _context, preskip = struct.unpack_from('<BBHIIII', block, 8)
    kind, data_size = struct.unpack_from('<II', block, data_at)
    if kind != 0x80000004:
        raise ValueError('no Switch Opus data chunk')
    at, end, packets = data_at + 8, data_at + 8 + data_size, []
    while at + 8 <= end:
        n = struct.unpack_from('>I', block, at)[0]
        if n == 0 or at + 8 + n > end:
            break
        packets.append(bytes(block[at + 8:at + 8 + n]))
        at += 8 + n
    return chans, rate, preskip, packets


def packet_samples(packet):
    """Samples (at 48 kHz) of an Opus packet, from its TOC byte."""
    toc = packet[0]
    config = toc >> 3
    if config < 12:
        size = (480, 960, 1920, 2880)[config & 3]
    elif config < 16:
        size = (480, 960)[config & 1]
    else:
        size = (120, 240, 480, 960)[config & 3]
    frames = {0: 1, 1: 2, 2: 2}.get(toc & 3)
    if frames is None:
        frames = packet[1] & 0x3f if len(packet) > 1 else 0
    return size * frames


def _crc_table():
    table = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04c11db7) if r & 0x80000000 else r << 1
        table.append(r & 0xffffffff)
    return table


_CRC = _crc_table()


def ogg_crc(data):
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xffffffff) ^ _CRC[((crc >> 24) & 0xff) ^ b]
    return crc


def _page(serial, seq, granule, packets, flags):
    lacing = b''
    for p in packets:
        lacing += b'\xff' * (len(p) // 255) + bytes([len(p) % 255])
    head = struct.pack('<4sBBqIII', b'OggS', 0, flags, granule, serial, seq, 0) + bytes([len(lacing)]) + lacing
    body = b''.join(packets)
    crc = ogg_crc(head + body)
    return head[:22] + struct.pack('<I', crc) + head[26:] + body


def ogg(block, serial=0x4f505553):
    """An Ogg Opus file of a Switch Opus block."""
    chans, rate, preskip, packets = switch_opus(block)
    head = struct.pack('<8sBBHIhB', b'OpusHead', 1, chans, preskip, rate, 0, 0)
    tags = b'OpusTags' + struct.pack('<I', 0) + struct.pack('<I', 0)
    pages = [_page(serial, 0, 0, [head], 0x02), _page(serial, 1, 0, [tags], 0)]
    granule, seq, group = 0, 2, []
    for i, p in enumerate(packets):
        group.append(p)
        granule += packet_samples(p)
        if len(group) == 50 or i == len(packets) - 1:
            pages.append(_page(serial, seq, granule, group, 0x04 if i == len(packets) - 1 else 0))
            seq += 1
            group = []
    return b''.join(pages)


# ---- PCM16 and DSP-ADPCM -> WAV

def _dsp(bwav, offset, frames, coefs):
    out, h1, h2 = [], 0, 0
    at = offset
    while len(out) < frames:
        header = bwav[at]
        scale, pair = 1 << (header & 0x0f), header >> 4
        c1, c2 = coefs[pair * 2], coefs[pair * 2 + 1]
        for i in range(14):
            if len(out) >= frames:
                break
            byte = bwav[at + 1 + i // 2]
            nibble = (byte >> 4) if i % 2 == 0 else (byte & 0x0f)
            if nibble >= 8:
                nibble -= 16
            s = ((nibble * scale) << 11) + 1024 + c1 * h1 + c2 * h2 >> 11
            s = max(-32768, min(32767, s))
            out.append(s)
            h2, h1 = h1, s
        at += 8
    return out


def wav(bwav, chans):
    rate, frames = chans[0][1], chans[0][2]
    pcm = []
    for codec, _rate, n, offset, coefs in chans:
        if codec == 0:
            pcm.append(list(struct.unpack_from(f'<{n}h', bwav, offset)))
        else:
            pcm.append(_dsp(bwav, offset, n, coefs))
    data = struct.pack(f'<{frames * len(pcm)}h', *(s for frame in zip(*pcm) for s in frame))
    fmt = struct.pack('<HHIIHH', 1, len(pcm), rate, rate * 2 * len(pcm), 2 * len(pcm), 16)
    return (b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVEfmt ' + struct.pack('<I', 16) + fmt
            + b'data' + struct.pack('<I', len(data)) + data)
