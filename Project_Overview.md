# SOYO SY-019L1 BIOS — Overview & Status

**Read this file first.** Detail lives in the other documents:
- `HOW_THE_PATCH_WORKS.md` — every byte the patch changes, with pseudo
  code for the translation and why the removed code is no longer needed
- `CHS_TRANSLATION_PORTING_GUIDE.md` — how the whole patch was done, written
  to be reused on a different BIOS
- `BIOS_ADDRESS_MAP.md` — verified address/structure reference for this ROM
- `BIOS_Evaluation.md` — root-cause analysis and the full bug history
- `Plan.txt` — what is still open

> **Final version only.** The patch went through five versions (v1 → v5)
> during development. This repository contains **only the final, hardware-
> confirmed version (v5)**; earlier ROMs, sources and build/test scripts
> were omitted for clarity, and file names no longer carry a version
> suffix. The bug history in `BIOS_Evaluation.md` and `Plan.txt` still
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

## 3. Status — WORKING

**Confirmed good on real hardware.**

| Drive | Physical CHS | Translated | Result |
|---|---|---|---|
| 8 GB CF (IEI ICF-1000IP) | 16000/16/63 | 1002/255/63, 7.67 GiB | FDISK, FORMAT /S, file copy, benchmark, **boots from C:** |
| WD Caviar 24300 | 8912/15/63 | 556/240/63 | FDISK, FORMAT, DIR, ScanDisk clean |
| 2 GB SanDisk CF | 3875/16/63 | 967/64/63 | DIR, ScanDisk clean |

Six bugs were found and fixed along the way (v1 -> v5); each one, its
symptom and its diagnosis is in `BIOS_Evaluation.md` §3-§7, and the
generalised lessons are in the porting guide §9.

Some other CF cards and CF-to-IDE adapters still misbehave. Emulation
shows the ROM produces correct task-file addresses for those geometries,
so the remaining failures are attributed to card/adapter compatibility
rather than the patch.

## 4. Hardware safety

No boot-block recovery on this BIOS generation. Socketed, reflashable
EPROM (27C512); keep a spare chip and the original dump (MD5 above).

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
