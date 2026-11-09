"""SARC archives (little endian): a 0x14-byte header, the SFAT node table (name hash, name attribute, data start and
end relative to the data section), the SFNT name pool, then the data section.

write keeps the header, the node table and the name pool as they are and lays the members out again in their old
order, each at an offset with at least the alignment its old offset had, so an archive written without changes is
byte-identical to the original."""
import struct

MAX_ALIGN = 0x2000


def _nodes(data):
    """(name, node offset, absolute start, absolute end) of every member, in node order."""
    if data[:4] != b'SARC' or data[6:8] != b'\xff\xfe':
        raise ValueError('not a little-endian SARC archive')
    header, size, start = struct.unpack_from('<HxxII', data, 4)
    if size != len(data) or data[header:header + 4] != b'SFAT':
        raise ValueError('SARC header does not match the file')
    count = struct.unpack_from('<H', data, header + 6)[0]
    pool = header + 12 + count * 16
    if data[pool:pool + 4] != b'SFNT':
        raise ValueError('SARC name pool missing')
    pool += 8
    out = []
    for i in range(count):
        node = header + 12 + i * 16
        _, attribute, lo, hi = struct.unpack_from('<4I', data, node)
        if not attribute >> 24:
            raise ValueError(f'SARC member {i} has no name')
        at = pool + (attribute & 0xffffff) * 4
        out.append((data[at:data.index(0, at, start)].decode('utf-8'), node, start + lo, start + hi))
    return out


def read(data):
    """[(name, bytes)] of every member, in node order."""
    return [(name, bytes(data[lo:hi])) for name, _, lo, hi in _nodes(data)]


def write(data, replace):
    """The archive with the members named in replace (name -> bytes) swapped for the new bytes."""
    nodes = _nodes(data)
    unknown = set(replace) - {n[0] for n in nodes}
    if unknown:
        raise KeyError(f'not in the archive: {", ".join(sorted(unknown))}')
    start = struct.unpack_from('<I', data, 12)[0]
    out = bytearray(data[:start])
    for name, node, lo, hi in sorted(nodes, key=lambda n: n[2]):
        rel = lo - start
        align = min(rel & -rel, MAX_ALIGN) if rel else MAX_ALIGN
        out += bytes(-(len(out) - start) % align)
        body = replace.get(name, data[lo:hi])
        struct.pack_into('<II', out, node + 8, len(out) - start, len(out) - start + len(body))
        out += body
    # the original's tail after its last member (padding) stays when nothing moved past it
    last_end = max((hi for _, _, _, hi in nodes), default=start)
    if not replace:
        out += data[last_end:]
    struct.pack_into('<I', out, 8, len(out))
    return bytes(out)
