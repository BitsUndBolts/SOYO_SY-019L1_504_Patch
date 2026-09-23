Soyo 386 AMI BIOS — Hard Disk Size Limit: Findings & Patch Design
=================================================================

**ROM:** 64 KB, AMI BIOS dated **11/11/92**. **Mapped at:** segment `F000`.
**Original MD5:** `e80a824d8f3c39d40b98a4be5d8d9cff`.

**Status: the floppy-boot-freeze bug is fixed and confirmed on real
hardware. A second, unresolved problem now blocks progress: translated
drives mostly fail to be recognized by DOS/FDISK, with exactly one
confirmed-working exception. This is the priority for the next session —
see §5.**

---

1. Root cause (unchanged)
--------------------------

Raw CHS pass-through, no translation. `AH=08h` clamps cylinders to
`0x3FF` (correct per spec) but the head field gets masked to 4 bits
(`AND DH,0Fh`) with nothing converting between logical and physical CHS.
Ceiling: 1024 × 16 × 63 × 512 = 504 MiB.

2. Patch design (unchanged)
----------------------------

Three new routines spliced into free space at `0x76F5` (294 bytes):
`calc_factor`, `xlate_getparams`, `xlate_headcyl`. Five call sites hooked:
`0xA659` (AH=08h body), `0xA965` (shared head-byte builder), and three
duplicated cylinder-high-byte port-write sites (`0xA60F`, `0xA7EB`,
`0xAB3E`). Full addresses and the INT13h dispatch table: see
`BIOS_ADDRESS_MAP.md`.

3. Bug #1 (found and fixed): `xlate_getparams` DX-clobber
-----------------------------------------------------------

`calc_factor`'s own documented contract says it clobbers `DX`. The
original code read `RealCyl` into `DX`, called `calc_factor`, then tried
to reuse `DX` afterward assuming it still held `RealCyl` — it didn't.
Result: `AH=08h` always reported 1023 cylinders, for every drive,
regardless of actual geometry. Confirmed by single-stepping the actual
assembled code in an emulator. **Fixed** by stashing `RealCyl` on the
stack (alongside `RealSectors`, which the code already did correctly)
instead of trusting `DX` to survive the call.

4. Bug #2 (found and fixed): off-by-one patch-region boundary
------------------------------------------------------------------

The `AH=08h` patch region was defined as `0xA659`–`0xA690` inclusive (56
bytes). **`0xA690` is a single-byte `RET` (`0xC3`) — the original
handler's own clean return.** It is *not* a fall-through into `AH=09h` (an
earlier working assumption in this project was wrong on this point). The
patch's NOP filler overwrote that `RET`, so every `AH=08h` call
accidentally kept executing straight into the entire `AH=09h` body
(drive-select, a ready-wait loop with a legitimately very slow worst-case
timeout, an `INT 15h` device-busy notification) as an unintended side
effect. This is what caused the floppy-boot freeze whenever any hard disk
was configured — confirmed by tracing the actual `INT 15h AH=90h`
"device busy" call and the subsequent worst-case ~65,536-iteration wait
loop this erroneous path fell into.

**Fixed** by shortening the patch region to `0xA659`–`0xA68F` (55 bytes),
which leaves the original `RET` at `0xA690` untouched.

**Diagnosis method, for reference:** a real-hardware bisection test (two
ROM variants, one patching only `AH=08h`, the other patching only the
read/write path) isolated the bug to the `AH=08h` site. From there,
comparing an instruction-level emulator trace of the original ROM against
the buggy patched one — both given the identical `AH=08h` call — showed
them diverging exactly at `0xA690`: the original hit `RET` and returned
in 91 instructions total; the buggy version fell through and needed over
5,000,000 instructions to (very slowly) resolve. This is the technique to
reach for first if another mismatch like this turns up: compare traces
against the *original* ROM under identical inputs, don't just reason about
the patched code in isolation.

**Both fixes confirmed on real hardware:** floppy now boots normally with
a hard disk configured, in every geometry tested, including drives where
the drive itself still isn't otherwise accessible from DOS (see §5) —
i.e. this specific bug is fully resolved independent of the newer problem.

5. Open problem: most translated drives fail DOS/FDISK recognition
-----------------------------------------------------------------------

With both bugs above fixed, drives were tested end-to-end (boot from
floppy, then access C: from DOS). Result:

| Drive | Real CHS | Factor | Trans. heads | Result |
|---|---|---|---|---|
| 250MB Type-47 | 998/16/32 | 1 | 16 | **Fails** — "Invalid Drive Specification", FDISK sees no fixed disk |
| 245MB SanDisk CF — freshly formatted **by the original, unpatched BIOS** (guaranteed-valid MBR + FAT16) | ~998/16/32-class | 1 | 16 | **Still fails** under the patched BIOS, identically |
| 1GB Transcend CF | 1986/16/63 | 2 | 32 | Fails |
| **2GB SanDisk CF** | **3875/16/63** | **4** | **64** | **Works** — `DIR`, ScanDisk both clean |
| WD Caviar HDD | 8912/15/63 | 16 | 240 | Fails |
| Transcend 4GB CF | 7899/16/63 | 8 | 128 | **POST itself fails** ("FDD controller failure") — never reaches DOS |

**Key finding that rules out the most obvious explanation:** the 245MB
drive was formatted fresh *by the original, unpatched BIOS* — so it is
guaranteed to have a valid MBR, partition table, and FAT16 filesystem
before ever touching the patched BIOS. It still fails identically to a
never-formatted blank drive. This rules out "the drive needs pre-existing
valid data" as the explanation for why the 2GB SanDisk works and nothing
else does.

**A hypothesis that was investigated and ruled out as the primary cause:**
this ROM contains a genuine boot-sector virus-protection feature —
confirmed via disassembly at `0xAB62`, called from the very start of the
`AH=03h` (Write Sectors) handler, checking for a write to logical
cylinder/head 0, sector 1 and branching into code that prints
`"Format !!!"` or `"BootSector Write !!!"` (strings at `0xA47F`/`0xA4A2`
respectively) via `INT 10h`, consistent with an interactive "continue?"
warning that a non-interactive FDISK write would not know how to answer.
This is real and will need to be handled before FDISK/FORMAT can *write*
a fresh partition table under the patched BIOS. **However, it only
triggers on writes (`AH=03h`), and the current failures are pure *read*
failures (`AH=02h`/DOS mount-time access) on a drive that already has
valid data** — so it does not explain the table above and is not today's
blocker. Revisit this once the read-path mystery is solved.

**What doesn't explain it (checked and ruled out this session):**
- Not a GetParams math error — verified via emulation that `AH=08h`
  reports mathematically correct translated values for every geometry in
  the table above.
- Not a repeated-call/state-corruption issue in `AH=08h` itself — tested
  calling it 3 times in a row per geometry in the emulator; results were
  identical and stable each time.
- Not explained by "already has valid data" (see the 245MB test above).

**Leads for the next session, roughly in order of promise:**

1. **The one thing that's different about the working case may be the
   translated head count itself (64), not anything about the specific
   drive.** Factors tested: 1, 2, 4 (works), 8 (POST fails), 16. It's
   worth testing intermediate/adjacent geometries that would *also*
   translate to 64 heads via a different real geometry, to see whether
   "64 translated heads" is the actual common thread rather than
   anything specific to the 2GB SanDisk card.
2. **Test the actual data path, not just `AH=08h`.** All existing
   emulator verification (`ide_harness.py`) has focused on `AH=08h` and a
   single `AH=04h` verify call. The failures happen when DOS mounts the
   drive at boot, which involves reading the MBR, then a boot sector,
   then FAT/root-directory sectors — a longer, more realistic sequence of
   `AH=02h` (and possibly multi-sector) calls than anything tested so
   far. Worth extending `ide_harness.py` to back its simulated drive with
   a real disk image (e.g. dd a small FAT-formatted image) and try
   reading through an actual boot sequence for a *failing* geometry
   (e.g. 998/16/32 or 1986/16/63) to see if something diverges partway
   through, the same way the `0xA690` bug was found by comparing traces.
3. **Consider a hardware/timing angle for the CF cards specifically**
   (though the WD Caviar failing too argues against this being the whole
   story): different CF card controllers can have different ATA timing
   tolerances, and our patch code is measurably slower (more
   instructions) between drive-select and command-issue than the
   original inline code it replaced.
4. Once reads work broadly, return to the boot-sector-protection finding
   above to make FDISK/FORMAT able to write a fresh partition table too.

`ide_harness.py` and `verify_model.py` remain the tools of choice for
testing hypotheses quickly before touching real hardware again.

6. v4 — ROOT CAUSE of §5 found (emulation-verified, awaiting real HW)
------------------------------------------------------------------------

**Bug #3 (the §5 blocker): AH=08h never sets DL = drive count.**
The v3 site-A NOP fill covered `0xA659-0xA68F`, but the patch then does
`JMP 0xA686` — and `0xA686-0xA68F` is the original handler tail
`mov dl,[0x75] / mov ah,0 / mov [0x74],ah` (only the `RET` at `0xA690`
survived). So DL came back as whatever `xlate_getparams`' last `DIV SI`
left in DX = **RealCyl mod factor**:

| Drive | RealCyl mod factor = DL returned | Observed |
|---|---|---|
| 998/16/32 (f=1) | 0 | fails |
| 1986/16/63 (f=2) | 0 | fails |
| **3875/16/63 (f=4)** | **3** | **works** |
| 8912/15/63 (f=16) | 0 | fails |
| 7899/16/63 (f=8) | 3 | POST fails (separate issue, see below) |

DL=0 = "zero fixed disks" -> DOS assigns no C:, FDISK says "No fixed
disks present". Matches the hardware table exactly. The earlier AH=08h
verification only checked CX/DH, never DL.
**Fix:** site A is now `0xA659-0xA685` (45 bytes); `0xA686-0xA690` is
left intact (asserted in build_v4.py).

**Bug #4: caller's DH corrupted on every translated call.** `[bp+15h]`
in the INT13h frame is not a dead byte — it is the *saved caller DH*
(frame: bp+14h=DX, +16h=CX, +18h=AX). The original ROM read DH bits 7:6
there as cylinder bits 11:10 (AMI ECHS convention; AH=08h also reports
them that way). v3 wrote physCyl>>8 into it, so every read/write/verify
returned a garbage DH, and reset/recal (which ran the translation on
garbage CX) returned DH=0xFF — breaking any caller that retries after
a reset (e.g. the BIOS boot loader at 0xF810).
**Fix:** xlate_headcyl writes the phys-cyl high byte to the frame local
`[bp+6]` (the 1F5h value; only ever written at the three port sites),
and the three sites now read `mov al,[bp+6]`.

**Bug #5: upper halves of EAX/ECX/EDX zeroed** (PUSHA/POPA around 32-bit
math). **Fix:** PUSHAD/POPAD.

**Cleanup:** translation only runs for CHS-addressed functions
(AH=02,03,04,05,0A,0B,0C). Everything else (reset, recal, test-ready,
alt-reset, park) gets the original ROM behaviour byte-for-byte
(head=DH&0Fh, 1F5h=(CL>>6)|((DH>>4)&0Ch)). Park (AH=19h) now seeks the
real landing zone again.

**Verification (ide_harness2.py / regress_v4.py):** for 8 geometries,
AH=08h returns DL=1, CF=0, correct translated CHS; 43 reads per
geometry hit exactly the expected LBA, never beyond disk end; CX, DX and
upper 32-bit halves preserved. For factor-1 drives, all functions
produce identical IDE register traffic to the original ROM.
v4 MD5: e0a33c21fe8d6bf70b956684c16d9aa1.

**Still open:**
- 7899/16/63 "FDD controller failure" at POST is NOT explained by
  emulation (2GB card also got DL=3 and POSTed fine). May disappear
  with bugs #4/#5 fixed; if not, check that 7899 cyl really matches the
  card's IDENTIFY geometry (POST verifies the last cylinder and retries
  for up to ~40 s on error).
- Drives with 16 heads and >8192 cyl translate to 256 heads (DH=255),
  which MS-DOS/Win9x cannot handle. Needs a cap at 255 (e.g. the
  15-head ECHS trick) to reach the 7.88 GB goal.
- Boot-sector write protection at 0xAB62 (unchanged).

7. v5 — 256-head cap (8 GB cards) + boot-path analysis
--------------------------------------------------------

**Confirmed working on real hardware with v4:** WD 24300 (8912/15/63,
240 translated heads) — FDISK, FORMAT, DIR and ScanDisk all clean on a
1.88 GB partition. So the read/write translation itself is sound across
factors 1..16.

**Bug #6: translated head count could reach 256** (the 8 GB failures).
`calc_factor` doubled heads while `heads*2 <= 256`, so any 16-head drive
with >8192 cylinders landed on 256 heads (DH=255 reported as max head
index). 256 heads is fatal for DOS: FDISK reports the correct size and
then fails with "Error writing fixed disk" (15506/16/63), and some
geometries hang before DOS even loads (16000/16/63).
**Fix (v5):** heads now go 16, 32, 64, 128 and then straight to **255**
(the Phoenix/AMI "Large"/ECHS table). Logical cylinders are no longer
`RealCyl / factor` but `RealCyl * RealHeads / TransHeads` (16-bit
`MUL`/`DIV`, no 32-bit registers touched), which is identical for every
power-of-two case, so **the geometry of every already-formatted drive is
unchanged** (997/16, 992/32, 967/64, 986/128, 556/240 all identical to
v4 — existing partitions stay valid). New ceiling: 1024 x 255 x 63 x 512
= 7.88 GB, the project goal.
- 15506/16/63 -> 971 cyl / 255 heads / 63 sect (7.44 GiB)
- 16000/16/63 -> 1002 cyl / 255 heads / 63 sect (7.68 GiB)
- 16383/16/63 -> 1024 cyl / 255 heads / 63 sect (7.84 GiB, clamped)

**Also in v5:** AH=08h now leaves AL = sectors/track, exactly as the
original handler did (v4 left the head count there; AL is undefined per
spec but free to match). `build_v5.py` now reads the routine entry
offsets out of the nasm listing instead of hard-coded constants, so the
call sites can't drift out of sync with the code again.

**Verification:** for an identity geometry (998/16/32, factor 1) v5 is
byte-for-byte identical to the ORIGINAL ROM in every register, flag,
BDA status byte and IDE port write, across all 15 INT 13h functions
(AH=00,01,02,04,05,08,09,0A,0C,0D,10,11,14,15,19). 44 reads per geometry
across 10 geometries hit exactly the right LBA with no register damage.

**Boot-from-C: analysis (open).** The ROM's boot path was traced and
emulated end to end (`boot_test.py` runs the real INT 19h handler at
`0xF758`):
- `0xF779` reads the boot-order CMOS bit; C:-first lands at `0xF7CA`.
- `0xF7CA` reads **CMOS 0Eh bit 3** ("fixed disk failed POST init"). If
  set, the hard disk is skipped *silently* and it boots the floppy.
  That bit is set at `0xAD7A` whenever POST's own AH=14h/09h/11h
  sequence fails, and cleared at `0xADBB` when it succeeds.
- Otherwise it reads the MBR (DL=80h, CX=0001h, ES:BX=0000:7C00), needs
  CF=0, CX!=0 and `55AA` at offset 1FEh, then jumps to 0000:7C00.
- If that fails it falls through to the floppy; if the floppy also fails
  you get "DISKETTE BOOT FAILURE". An INT 18h raised by MBR code (rather
  than by the ROM at 0xF7AD) lands on "NO ROM BASIC" + halt.
In emulation the whole path works under v4/v5: MBR read issued at LBA 0,
signature accepted, jump to 0000:7C00 taken. POST's HDD init sequence
(AH=14h, 09h, 11h, 08h, 04h at the last cylinder) also returns CF=0 for
every geometry. So nothing in the ROM boot path is provably broken yet —
the next data point has to come from the screen: with no floppy in the
drive and boot order C:,A:, the message distinguishes the cases
("DISKETTE BOOT FAILURE" = ROM never accepted the MBR; "NO ROM BASIC" /
halt = MBR ran but found no ACTIVE partition; "Missing operating system"
/ "Invalid partition table" = MBR-level; "Non-System disk or disk error"
= boot sector ran but IO.SYS load failed). Also worth ruling out first:
the partition must be marked Active in FDISK option 2.

**Stack note (checked, not a problem):** the BIOS POST/boot stack is
SS:SP=0030:0100 (linear 0x300-0x400) and the type-47 drive table sits
at 0:300 immediately below it. Peak INT 13h stack use: original 56
bytes, v4/v5 96 bytes (low-water linear 0x3A0) — still ~128 bytes clear
of the drive table.
v5 MD5: 8e520e91100f932d3f054883b08d37da.

8. v5 CONFIRMED ON REAL HARDWARE — project goal reached
---------------------------------------------------------

8 GB CompactFlash (IEI Technology ICF-1000IP), physical 16000/16/63
entered as type 47, under v5:

- Translated geometry **1002 cyl / 255 heads / 63 sectors = 7.67 GB**,
  exactly as predicted by emulation.
- FDISK created a partition of the full size; a foreign (Linux) boot
  record left on the card by a previous system had to be cleared first.
- `FORMAT C: /S` completed: 7,851.73 MB total, 7,851.51 MB available,
  4 KB clusters, 2,009,985 allocation units (FAT32 under MS-DOS 7.1).
- File copy, `DIR`, and a full benchmark pass (linear read/verify sweep
  to track 985 of 1002 — i.e. the far end of the disk, which is what
  proves the physical cylinder high byte is right) all clean.
- **Boots directly from C:**.
- WD Caviar 24300 (8912/15/63 -> 556/240/63) and 2 GB SanDisk CF
  (3875/16/63 -> 967/64/63) unchanged and still working, as intended:
  v5's geometry change only affects cases that would have needed 256
  heads.

**Remaining failures are attributed to hardware, not the ROM.** Several
other CF cards and at least one CF-to-IDE adapter still misbehave, while
emulation shows the patched ROM issues correct task-file addresses for
those geometries. The earlier "cannot boot from C:" reports fall into the
same bucket, compounded by leftover foreign boot records on the media.
Diagnostic order for any future case: (1) is the partition Active,
(2) does the media carry a foreign MBR, (3) does POST report a disk
error (CMOS 0Eh bit 3 makes the ROM skip booting from C: silently),
(4) try a different card/adapter, and only then (5) suspect the patch.

The reusable write-up of the whole method — including the pitfalls behind
bugs #1-#6 — is in `CHS_TRANSLATION_PORTING_GUIDE.md`.
