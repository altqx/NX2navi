"""The game's RomFS as tools/make_game.py leaves it in an install folder: romfs/romfs.bin, one image of every file, and
romfs/index.json, each file's path, offset and size in it. Files ending in .zs are zstd frames, most of them made with
one of the dictionaries in Pack/ZsDic.pack.zs; a frame names its dictionary by id."""
import json
from pathlib import Path

try:
    import compression.zstd as zstd
except ImportError:  # before Python 3.14
    import zstd_compat as zstd  # noqa: F401  (tools/ is on the path of tools/translate.py)

from . import sarc

ZSTD_LEVEL = 19


class Game:
    def __init__(self, folder):
        self.folder = Path(folder).expanduser()
        romfs = self.folder / 'romfs'
        index = romfs / 'index.json'
        if not index.is_file():
            raise FileNotFoundError(f'{index} is missing: give the folder tools/make_game.py built the game into')
        self.files = {f['path']: (f['offset'], f['size']) for f in json.loads(index.read_text(encoding='utf-8'))['files']}
        self.image = romfs / 'romfs.bin'
        self._dictionaries = None

    def raw(self, path):
        """The file's bytes as stored (compressed when its name ends in .zs)."""
        offset, size = self.files[path]
        with open(self.image, 'rb') as f:
            f.seek(offset)
            data = f.read(size)
        if len(data) != size:
            raise OSError(f'{self.image} ends inside {path}')
        return data

    def dictionaries(self):
        """Dictionary id -> zstd dictionary, from Pack/ZsDic.pack.zs (itself compressed without one)."""
        if self._dictionaries is None:
            self._dictionaries = {}
            for _, data in sarc.read(zstd.decompress(self.raw('Pack/ZsDic.pack.zs'))):
                d = zstd.ZstdDict(data)
                self._dictionaries[d.dict_id] = d
        return self._dictionaries

    def _dictionary_of(self, frame):
        dict_id = zstd.get_frame_info(frame).dictionary_id
        return self.dictionaries()[dict_id] if dict_id else None

    def read(self, path):
        """The file's contents, decompressed when its name ends in .zs."""
        data = self.raw(path)
        if not path.endswith('.zs'):
            return data
        return zstd.decompress(data, zstd_dict=self._dictionary_of(data))

    def store_like(self, path, contents):
        """contents stored the way the game's file at path is: compressed with the same dictionary when its name ends
        in .zs, so the game's loader takes the new file as it takes its own."""
        if not path.endswith('.zs'):
            return contents
        dictionary = self._dictionary_of(self.raw(path))
        data = zstd.compress(contents, level=ZSTD_LEVEL, zstd_dict=dictionary)
        if zstd.decompress(data, zstd_dict=dictionary) != contents:
            raise AssertionError(f'{path}: the compressed file does not read back')
        return data
