# Project Overview

**Read this file first** if you want to work on the code or use the
repository as a reference. [`README.md`](README.md) is the short
introduction; this file says what is where and what to keep in mind.

## 1. What this repository is

A rebuilt AMI BIOS (11/11/92) for the SOYO SY-019L1 386 board, in two
parts:

| Part | Folder | ROM | MD5 |
|---|---|---|---|
| **ECHS large-disk patch v5**: lifts the 504 MB limit to the INT 13h CHS ceiling of 8.42 GB (7.84 GiB), no LBA | repository root | `binary/SY019L1_27C512_CHSPATCH.BIN` | `8e520e91100f932d3f054883b08d37da` |
| **BUBios 2.1**: the ECHS patch plus a new setup, a POST screen with boot countdown, IDE auto-detection, INT 13h extensions (LBA, up to 128 GB) and a CF-card floppy fix | `BUBios/` | `BUBios/binary/BUBIOS2_SY019L1.BIN` | `b975393064d7b8a387dd7bf6e5d32286` |

**Status: complete.** Both ROMs are final and confirmed on the real
board. Their build scripts reproduce them byte for byte; treat the
binaries as the reference, not as something to regenerate.

BUBios is built on top of the ECHS ROM and leaves its disk code
byte-identical. Its folder is self-contained: `BUBios/binary/base/` holds
copies of the original and ECHS ROMs, and `BUBios/py/` holds copies of
the ECHS emulator and tests.

## 2. Where to find what

| If you want to … | Read |
|---|---|
| know what BUBios does, and how | [`BUBios/README.md`](BUBios/README.md) |
| know why a BUBios detail is the way it is | [`BUBios/CHANGELOG.md`](BUBios/CHANGELOG.md) (every build, every bug found on the board) |
| understand every byte of the ECHS patch | [`docs/HOW_THE_PATCH_WORKS.md`](docs/HOW_THE_PATCH_WORKS.md) |
| look up an address in this ROM | [`docs/BIOS_ADDRESS_MAP.md`](docs/BIOS_ADDRESS_MAP.md) (§1-§8 original and ECHS, §9 BUBios) |
| follow how the ECHS bugs were found | [`docs/BIOS_Evaluation.md`](docs/BIOS_Evaluation.md) (development log) |
| add ECHS translation to another BIOS | [`docs/CHS_TRANSLATION_PORTING_GUIDE.md`](docs/CHS_TRANSLATION_PORTING_GUIDE.md) |
| add LBA, a POST screen or a new setup to another BIOS | [`docs/BIOS_MODDING_GUIDE.md`](docs/BIOS_MODDING_GUIDE.md) |
| see the ECHS version history | [`CHANGELOG.md`](CHANGELOG.md) |

## 3. Board and ROM

- **Board:** SOYO SY-019L1, 386DX-40, OPTi 82C495SLC chipset, tested with
  16 and 64 MB RAM and a Cyrix FasMath coprocessor.
- **BIOS:** AMI, dated 11/11/92. 64 KB ROM (27C512) at segment `F000`.
  Pure CHS, pre-LBA. File offset = offset in segment `F000`.
- **Original ROM:** `binary/SY019L1_27C512_ORIGINAL.BIN`,
  MD5 `e80a824d8f3c39d40b98a4be5d8d9cff`.

## 4. The ECHS patch

Raw CHS pass-through with a 4-bit head field (`AND DH,0Fh`) capped the
board at 1024 x 16 x 63 x 512 = 504 MiB. The patch adds bit-shift (ECHS /
"Large") translation: a logical geometry is reported to DOS from AH=08h
and converted back to physical CHS on every access, with the head ladder
16, 32, 64, 128, 255. Ceiling: 1024 x 255 x 63 x 512 = **7.84 GiB
(8.42 GB)**, the INT 13h CHS limit and the original project goal.

All of it fits in unused space: 353 bytes of new code at `0x76F5`, five
small patch sites, and one checksum word.

**Confirmed on the board:**

| Drive | Physical CHS | Translated | Result |
|---|---|---|---|
| 8 GB CF (IEI ICF-1000IP) | 16000/16/63 | 1002/255/63, 7.67 GiB | FDISK, FORMAT /S, file copy, benchmark, **boots from C:** |
| WD Caviar 24300 | 8912/15/63 | 556/240/63 | FDISK, FORMAT, DIR, ScanDisk clean |
| 2 GB SanDisk CF | 3875/16/63 | 967/64/63 | DIR, ScanDisk clean |

Six bugs were found and fixed on the way (v1 → v5); each one, its symptom
and its diagnosis is in `docs/BIOS_Evaluation.md` §3-§7, and the
generalised lessons are in the porting guide §9.

**Compatibility note.** A few other CF cards and CF-to-IDE adapters did
not work on this board. Emulation shows that the ROM produces the correct
task-file addresses for those geometries, so these are card or adapter
compatibility problems rather than a fault in the patch. If a drive
doesn't work, check these in order:

1. Is the partition marked Active?
2. Does the media still carry a boot record from another system?
3. Does POST report a disk error? CMOS 0Eh bit 3 makes the ROM skip
   booting from C: without any message.
4. Does a different card or adapter work?

`docs/BIOS_Evaluation.md` §8 explains each check.

## 5. BUBios

BUBios replaces AMI's setup screens with an MR-BIOS-style program (tabs,
Summary page, typed date and time, colour schemes) and AMI's start-up
text with a full-screen POST display. It adds:

- a 3-second boot countdown with DEL for setup, and a restart of POST
  when settings were changed in setup
- a clean screen for DOS
- automatic IDE detection at boot (a Tools page switch, off by default)
- dropping configured drives that are not connected
- the INT 13h extensions (EDD 1.1, 28-bit LBA, up to 128 GB)
- "no change line" for the floppy while a CF card is on the IDE bus
  (the card hides the change line, so MS-DOS missed floppy swaps)

AMI's dead code (the "System Configuration" box and the Hard Disk
Utility, 4,501 bytes) was removed to make room. **About 10 bytes of the
ROM are left.**

**Confirmed on the board** with the release (2.1, seventh build): 16 and
64 MB RAM, a 20 GB master and an 80 GB Samsung (auto-detect, LBA), 4 GB
and 16 GB CF cards (the 16 GB card reports 31045/16/63), Windows 95 B and
MS-DOS 7.1, the countdown, setup visits, auto-detect, unplugged drives and
floppy swaps with a CF card. All reported bugs are closed; the history is
in `BUBios/CHANGELOG.md`.

## 6. Hardware safety

This BIOS generation has no boot-block recovery. The EPROM (27C512) is
socketed and can be reprogrammed, so keep a spare chip and the original
dump (MD5 above). This applies to both ROMs.

## 7. Tooling

The builds need NASM and Python 3; the emulator tests need
`pip install unicorn` (and `pillow` for BUBios screenshots).

**ECHS patch** (run from the repository root):

- `py/build.py`: assembles `asm/xlate.asm`, splices it into the free
  space at `0x76F5`, patches the five call sites, fixes the ROM checksum
  and checks the result against the hardware-confirmed MD5. Entry offsets
  are read from the NASM listing (`asm/xlate.lst`), so they cannot drift.
- `py/ide_harness.py`: Unicorn-based real-mode harness. Runs genuine INT
  13h calls through the real dispatcher against either ROM, with a
  simulated IDE drive (optionally backed by sector data), CMOS, IVT
  dispatch, IDE/LBA logging and a stack low-water mark.
- `py/regress.py`: 10 geometries. Pinned AH=08h geometry, 44 reads each
  checked against the expected LBA, register preservation, and identical
  behaviour to the original ROM for drives that need no translation.
- `py/boot_test.py`: runs the ROM's own INT 19h boot path.
- `py/verify_model.py`: the translation math in plain Python.
- `py/analyze_bios.py`: reproduces the ROM-structure facts in the
  address map.

**BUBios** (run from `BUBios/`):

- `py/build_bubios.py`: builds `binary/BUBIOS2_SY019L1.BIN` from
  `binary/base/`, asserting every patch site, reused AMI string and
  record, and dead-code range.
- `py/test_bubios.py` (74 checks, setup emulator), `py/test_post.py`
  (94 checks, full POST emulator, 25-35 minutes), `py/regress_echs.py`
  (the ECHS tests on the BUBios ROM).
- `py/setup_emu.py`, `py/post_emu.py`, `py/int13x.py`: the emulators.
- `py/shots.py`: setup screenshots.
- `asm/lba_test_mbr.asm`: a boot sector that tests the INT 13h
  extensions on a scratch disk.
- `tools/DSKCHG.COM`: a DOS tool that shows the floppy change line live.

## 8. Rules for changing the code

- **Keep the geometry mapping stable.** If you change how the logical
  geometry is computed, every geometry that already works must still map
  to exactly the same numbers; otherwise existing partitions become
  unreadable. `py/regress.py` and `py/verify_model.py` pin those numbers
  and fail if they change.
- **Keep the disk code identical in BUBios.** `BUBios/py/regress_echs.py`
  must pass unchanged.
- **The ROM is full.** New BUBios features need space reclaimed first
  (see `BUBios/README.md`, *Picking this up again*, and the modding guide
  §2).
- **Assert, don't assume.** Both build scripts check the original bytes
  of every site they patch. Keep it that way for any new site.
- **Early POST traps** (details in `BUBios/README.md`, *How it works*):
  interrupts are off, absent drives are only final after the memory test,
  and AMI's DEL flag lives in CMOS 0Eh bit 0.
- **Boot-sector virus protection** is an original AMI feature (CMOS
  option, check routine at `0xAB62`). If FDISK or FORMAT ever stop with a
  "Format !!!" or "BootSector Write !!!" warning, turn the option off in
  setup.
- **Ceiling without LBA.** Through CHS (and ECHS), DOS sees at most
  1024/255/63 = 7.84 GiB. Only BUBios's INT 13h extensions reach beyond.
