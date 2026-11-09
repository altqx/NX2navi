# Fan translations

`tools/translate.py` (package `tools/translation/`) turns the game's text, fonts and lettered textures into a
translation project and builds the translation into a player mod. No recompiled code changes: the runtime serves a
mod's RomFS files in place of the game's (`runtime/romfs.c` mods_overlay_init) and raises the resource size table for
the ones that grew. It reads the game from an install folder made by `tools/make_game.py` (`romfs/romfs.bin` and
`romfs/index.json`); the project folder lives outside the repository, since it holds the game's text and images.

```sh
.venv-py314/bin/python tools/translate.py init ~/oot-thai --game ~/Games/Ocarina --slot JPja --reference USen --mod Thai
.venv-py314/bin/python tools/translate.py fonts --project ~/oot-thai --compare /path/to/ThaiFont-Regular.ttf
.venv-py314/bin/python tools/translate.py check --project ~/oot-thai
.venv-py314/bin/python tools/translate.py build --project ~/oot-thai     # -> <game>/mods/Thai
```

The translation replaces one game language (the slot); the player picks that language (`nx2navi.ini`, `[language]`,
`lang = ja` for `JPja`). The voices follow the game language too (`Voice/Resource/<lang>_*`), so the Japanese slot
keeps the Japanese voices. The project's `README.md` is the translator's guide.

## What the game has

- Text: `Mals/<lang>.Product.110.sarc.zs`, 540 MSBT files each, UTF-8 (header byte 0x0c is 0), sections LBL1 and
  TXT2. Control codes are 0x0e, u16 group, u16 type, u16 parameter size, parameters; 0x0f, group, type closes one.
  Group 0 type 0 is a reading (furigana): u16 character count, u16 byte length, UTF-8. `tools/translation/msbt.py`
  turns them into a markup a translator edits; the selftest checks that all 338,208 messages of the 16 languages
  survive it byte for byte.
- `.zs` files are zstd frames; most use one of the dictionaries in `Pack/ZsDic.pack.zs`, named by id in the frame. A
  replaced file is compressed with the dictionary of the file it replaces.
- Fonts: `Font2/Scalable/*.bfotf|bfttf` (an 8-byte header, then the font with every byte from offset 4 XORed with a
  4-byte key that the size word gives away), and per language group a style archive `Font2/Font*.bfarc` of `.buif`
  styles. A style is an ordered list of entries: a font, the code point ranges it serves, x and y scale, baseline
  shift. A character a font lacks goes on to the next entry that covers it. In the Japanese and European archive
  (`Font`) Cetra Display Pro serves every character before the Japanese fonts, which are CID-keyed; new letters
  merged into its two weights therefore reach every text style. `reference/fonts.md` of a project lists them all.
- Lettered textures: the boss title cards, `UI/CustomTex/BossName/Enemy_DungeonBoss_<boss>[_Glow|_Sh]_<lang>.bntx.zs`
  (10 bosses, 15 languages), BC4 1792x565. The glow and shadow layers follow max(letters, gain * Gaussian blur of the
  letters) within a few levels (radius 8 to 10, gain 1 for the glow, about 1.6 for the shadow), so the build draws
  them from an edited letters layer, fitting radius and gain to the game's own three layers.
- Not handled: text inside movies (`Event/Movie/*.webm`, the credits among them) and textures of other formats than
  BC4 and RGBA8 (an edited file can still go into the project's `romfs/` as it is).

## Thai

The game's text engine draws each character at its own place: Thai vowel and tone marks then collide with tall
letters, with each other and with letters' tails. The build shapes every cluster the translation uses (a letter with
its marks, `fonts.CLUSTER`) with the Thai font's own rules (HarfBuzz through uharfbuzz), adds the result to the game
font as one glyph at a private-use code point (U+E800..U+F6FF; the game's own button icons are U+E040..U+E09B) and
writes those code points into the text. The PO files keep ordinary Thai.

## Checks

`tools/translate.py selftest --game <install folder>` reads and writes back every text archive, font and lettered
texture of the game (the BC4 re-encode within a few levels). `tests/translation_test.py` covers the formats on
made-up files, without game data.
