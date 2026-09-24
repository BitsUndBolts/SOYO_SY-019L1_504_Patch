# SOYO SY-019L1 BIOS — Overview

**Read this file first.** Detail lives in the other documents:
- `HOW_THE_PATCH_WORKS.md` — every byte the patch changes, with pseudo
  code for the translation and why the removed code is no longer needed
- `CHS_TRANSLATION_PORTING_GUIDE.md` — how the whole patch was done, written
  to be reused on a different BIOS
- `BIOS_ADDRESS_MAP.md` — verified address/structure reference for this ROM
- `BIOS_Evaluation.md` — root-cause analysis and the full bug history
- `CHANGELOG.md` — version history of the ECHS patch (v1 → v5)
- `BUBios/README.md` — stage 2: the BUBios setup UI built on top of the patch
- `BUBios 2.0/README.md` — stage 3: BUBios 2.1 (POST screen, boot countdown,
  disk auto-detect, LBA / INT 13h extensions), complete and confirmed on the
  board. Its section *Status* is the place to pick up from.

> **Final version only.** The patch went through five versions (v1 → v5)
> during development. This repository contains **only the final, hardware-
> confirmed version (v5)**; earlier ROMs, sources and build/test scripts
> were omitted for clarity, and file names no longer carry a version
> suffix. The bug history in `BIOS_Evaluation.md` and `CHANGELOG.md` still
> refers to the intermediate versions (v3, v4, …) in which each bug
> appeared, because that is how it was found and fixed.

---

## 1. Board / ROM identification

- **BIOS:** AMI, dated 11/11/92. OPTi 82C495SLC chipset. 64 KB ROM at
  segment `F000`. Pure CHS, pre-LBA. 386DX-40, 16 MB RAM.
- **Original ROM:** `binary/SY019L1_27C512_ORIGINAL.BIN`,
  MD5 `e80a824d8f3c39d40b98a4be5d8d9cff`.
- **Patched ROM:** `binary/SY019L1_27C512_CHSPATCH.BIN` (v5),
  MD5 `8e520e91100f932d3f054883b08d37da`.

## 2. Goal and approach

Raw CHS pass-through with a 4-bit head field (`AND DH,0Fh`) capped the
board at 1024 x 16 x 63 x 512 = 504 MiB. The patch adds bit-shift (ECHS
/ "Large") translation: a logical geometry is reported to DOS from
AH=08h and converted back to physical CHS on every access, with the head
ladder 16, 32, 64, 128, 255. Ceiling: 1024 x 255 x 63 x 512 =
**7.84 GiB (8.42 GB)** — the INT 13h CHS limit, and the project goal.

All of it fits in the existing 64 KB EPROM: 353 bytes of new code in
unused space at `0x76F5`, five small patch sites, and one checksum word.

## 3. Status — complete

**Confirmed good on real hardware.**

| Drive | Physical CHS | Translated | Result |
|---|---|---|---|
| 8 GB CF (IEI ICF-1000IP) | 16000/16/63 | 1002/255/63, 7.67 GiB | FDISK, FORMAT /S, file copy, benchmark, **boots from C:** |
| WD Caviar 24300 | 8912/15/63 | 556/240/63 | FDISK, FORMAT, DIR, ScanDisk clean |
| 2 GB SanDisk CF | 3875/16/63 | 967/64/63 | DIR, ScanDisk clean |

Six bugs were found and fixed along the way (v1 -> v5); each one, its
symptom and its diagnosis is in `BIOS_Evaluation.md` §3-§7, and the
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

`BIOS_Evaluation.md` §8 explains each check.

## 4. Hardware safety

This BIOS generation has no boot-block recovery. The EPROM (27C512) is
socketed and can be reprogrammed, so keep a spare chip and the original
dump (MD5 above). This applies to both ROMs in this repository.

## 5. Tooling

Run everything from the repository root. The build needs NASM; the
emulator tests need `pip install unicorn`.

- `py/build.py` — assembles `asm/xlate.asm`, splices it into the free
  space at `0x76F5`, patches the five call sites, fixes the ROM checksum
  and checks the result against the hardware-confirmed MD5. Entry offsets
  are read from the NASM listing (`asm/xlate.lst`), so they cannot drift.
- `py/ide_harness.py` — Unicorn-based real-mode harness: runs genuine INT
  13h calls through the real dispatcher against either ROM, with a
  simulated IDE drive (optionally backed by sector data), CMOS, IVT
  dispatch, IDE/LBA logging and a stack low-water mark.
- `py/regress.py` — 10 geometries: pinned AH=08h geometry, 44 reads each
  checked against the expected LBA, register preservation, and identical
  behaviour to the original ROM for drives that need no translation.
- `py/boot_test.py` — runs the ROM's own INT 19h boot path.
- `py/verify_model.py` — the translation math in plain Python.
- `py/analyze_bios.py` — reproduces the ROM-structure facts in the
  address map.

## 6. Limits and notes for modifying the patch

- **Ceiling.** 1024 x 255 x 63 x 512 = 7.84 GiB (8.42 GB) is the INT 13h
  CHS limit. Larger drives work, but only their first 7.84 GiB can be
  used. Going beyond that needs the INT 13h extensions (AH=41h/42h). The
  ECHS ROM does not have them; BUBios 2.1 (stage 3) adds them.
- **Boot-sector virus protection.** This is an original AMI feature (CMOS
  option, check routine at `0xAB62`). If FDISK or FORMAT ever stop with a
  "Format !!!" or "BootSector Write !!!" warning, turn the option off in
  setup.
- **Keep the geometry mapping stable.** If you change how the logical
  geometry is computed, every geometry that already works must still map
  to exactly the same numbers; otherwise existing partitions become
  unreadable. `py/regress.py` and `py/verify_model.py` pin those numbers
  and fail if they change.

## 7. Stage 2 — BUBios

`BUBios/` builds on this ROM and replaces the AMI setup screens with an
MR-BIOS-style interface. It has tabs and a Summary page (CPU, measured
clock, coprocessor, memory, and each disk with its physical and ECHS
geometry), and lets you type the date and time as numbers. The disk code
is unchanged. `BUBios/binary/BUBIOS_SY019L1.BIN` is the ROM to program
if you want both. See `BUBios/README.md`.

## 8. Stage 3 — BUBios 2.x (complete)

`BUBios 2.0/` holds BUBios 2.1. It is built from the ECHS ROM plus the
BUBios setup and adds:

- a full-screen POST display
- a 3-second boot countdown with DEL for setup
- a clean screen for DOS
- automatic IDE detection (Tools page switch)
- the INT 13h extensions (EDD 1.1, 28-bit LBA, up to 128 GB)

AMI's dead Hard Disk Utility code was removed to make room; about 80 bytes
of the 64 KB ROM are left.

- **ROM:** `BUBios 2.0/binary/BUBIOS2_SY019L1.BIN`. `BUBios 2.0/CHANGELOG.md`
  gives the MD5 of each build.
- **Confirmed on the board** (builds 1-4):
  - 64 MB RAM
  - a 20 GB master and an 80 GB Samsung (LBA)
  - a 16 GB CF card (reports 31045/16/63)
  - a 4 GB slave (ECHS)
  - Windows 95 B installed
  - the countdown, auto-detect and setup visits
- **Status: complete.** The seventh build (release) is confirmed on the
  board. All reported bugs are closed:
  - a hang at the video sign-on after power-on or RESET
  - setup visits before and during the countdown
  - 64 MB memory display
  - unplugged drives stalling POST
  - "Entering Setup" feedback after DEL
  - floppy swaps not noticed under MS-DOS while a CF card is on the IDE bus
    (the card hides the change line; the BIOS now reports "no change line"
    so DOS checks the disk itself)
- **ROM space:** practically full (about 10 bytes left).
- **If you continue:**
  1. Read `BUBios 2.0/README.md`, section *Status*.
  2. Build with `python3 py/build_bubios.py`, run from `BUBios 2.0/`.
  3. Test with `py/test_bubios.py`, `py/test_post.py` (POST emulator) and
     `py/regress_echs.py`.
