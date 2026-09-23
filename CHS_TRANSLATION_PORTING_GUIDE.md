# Porting a CHS-Translation Patch to Another Legacy BIOS

**What this documents:** the complete method used to lift a 1992 AMI 386
BIOS (SOYO SY-019L1, OPTi 82C495SLC, 64 KB ROM) from a 504 MB hard-disk
ceiling to the INT 13h CHS ceiling of 7.84 GiB (8.42 GB), with no LBA support and
no extra ROM space. Verified on real hardware: an 8 GB CF card
(16000/16/63) partitioned, formatted, booted from, and benchmarked.

Every BIOS is different, but the *structure* of the problem is always the
same. This guide is organised as: the theory, then how to find the parts
in an unknown ROM, then what to write, then how to prove it works, then a
list of the traps that cost us the most time. If you only read one
section, read **§9 Pitfalls** — every item there is a bug we actually
shipped and had to diagnose on hardware.

---

## 1. Why the limit exists

Two different CHS interfaces meet inside the BIOS, and each has its own
field widths:

| Interface | Cylinders | Heads | Sectors | Max capacity |
|---|---|---|---|---|
| INT 13h (software side) | 1024 (10 bits: CH + CL bits 7:6) | 256 (DH) | 63 (CL bits 5:0) | **7.88 GiB** (8.46 GB) |
| ATA/IDE task file (hardware side) | 65536 (1F4h + 1F5h) | 16 (4 bits in 1F6h) | 255 (1F3h) | 136 GB |

An unpatched legacy BIOS passes the INT 13h values **straight through** to
the task file. The addressable space is then the *intersection* of the two:
1024 x 16 x 63 x 512 = **504 MiB**. Nothing is broken; there is simply no
translation layer.

The fix is to invent a **logical geometry** that fits INT 13h, report it
to DOS from AH=08h, and convert logical to physical on every access:

```
heads_logical = heads_physical * 2^n   (16, 32, 64, 128, then 255)
cyls_logical  = cyls_physical * heads_physical / heads_logical
sectors       = unchanged
```

This is "bit-shift" / ECHS / "Large" translation. The sector-linear
address is preserved, which is what makes multi-sector transfers across
track and cylinder boundaries keep working for free:

```
LBA      = (cyl_log * heads_log + head_log) * sectors + (sector_log - 1)
sector_p = (LBA mod sectors) + 1
head_p   = (LBA / sectors) mod heads_phys
cyl_p    =  LBA / sectors / heads_phys
```

**The physical cylinder now needs a full 8-bit high byte** (up to 16383),
so the hardware-side cylinder-high register (1F5h) must be fed a real
byte, not the 2 bits that INT 13h's CL carries. That is a separate patch
site and is easy to overlook (see §6, sites 3-5).

---

## 2. Choosing the logical geometry (and the 255-head rule)

Pick the smallest head count that brings cylinders under 1024:

```
heads = heads_phys                 ; usually 16
cyls  = cyls_phys
while cyls > 1024:
    if heads > 127: heads = 255; break     ; <-- never 256
    heads *= 2
    cyls  /= 2
cyls_logical = cyls_phys * heads_phys / heads          ; exact, not the shifted value
```

**Never allow 256 heads.** DH would be reported as 255 = "256 heads", and
DOS/Win9x cannot handle it: FDISK displays the correct capacity and then
fails with "Error writing fixed disk", and some geometries hang before DOS
loads. Real BIOSes use the ladder 16, 32, 64, 128, **255**. Because 255 is
not a power of two, compute the logical cylinder count as
`cyls_phys * heads_phys / heads_logical` (a 16x16->32 `MUL` followed by a
32/16 `DIV` — the quotient always fits, so no 32-bit registers are needed).

Ceiling with this rule: 1024 x 255 x 63 x 512 = **7.84 GiB** (8.42 GB). Going beyond
that requires INT 13h extensions (AH=41h/42h), i.e. a different project.

Keep whatever "-1" / "-2" convention the original AH=08h code used when
reporting the maximum cylinder index, so reported capacity stays
consistent with the untouched ROM.

---

## 3. Tools

- **A dump of the original ROM** plus its MD5. Everything is compared
  against this. Keep a spare programmed EPROM; BIOSes of this era have no
  boot-block recovery.
- **ndisasm** (`ndisasm -b16 -o <addr> slice.bin`) for quick, honest
  16-bit disassembly of arbitrary slices; Ghidra if you want a full
  cross-referenced database. A hex dump of the whole ROM is useful for
  byte-pattern searches.
- **nasm** to assemble the new routines (`BITS 16`, `CPU 386`, `ORG <free
  space address>`).
- **Python** for the build script (splice + patch + checksum) and for
  pattern searches (find every `E8`/`E9` whose target is a routine of
  interest; find every `CD 13`).
- **Unicorn Engine** for an emulator harness that runs the real ROM. This
  is the single highest-value tool in the project: it found four of the
  six bugs before they ever reached hardware, and it is the only practical
  way to A/B a patched ROM against the original.

---

## 4. Mapping an unknown ROM

Work top-down; each step gives you the next one's address.

**4.1 Reset vector and layout.** The last 16 bytes hold `JMP FAR`. File
offset == segment offset when the ROM is 64 KB at F000.

**4.2 The INT 13h entry point.** Look for the classic prologue:

```
cmp dl,80h
jc  <floppy>        ; forwards to INT 40h for drives < 80h
or  ah,ah
jnz <skip>
int 40h             ; AH=00 resets the floppy too
call <cache/shadow hook>
push ax cx dx bx si di ds es
sub  sp,8           ; <- local scratch used to build the task file
push bp
mov  bp,sp
```

**4.3 Write down the stack frame. This is the most important table you
will make.** From the push order above, with `bp` at the saved `bp`:

| Offset | Contents |
|---|---|
| `[bp+00]` | caller's BP |
| `[bp+02..09]` | 8 scratch bytes (precomp, sector count, cyl lo, cyl hi, drive/head, command) |
| `[bp+0A]` | ES |
| `[bp+0C]` | DS |
| `[bp+0E]` | DI |
| `[bp+10]` | SI |
| `[bp+12]` | BX |
| `[bp+14]` | **DX** (`[bp+15]` = caller's DH) |
| `[bp+16]` | CX |
| `[bp+18]` | AX |
| `[bp+1A..]` | IP, CS, FLAGS |

Everything the handler does to "return" a value is a write into this
frame. Everything you must **not** disturb is also in this frame. In our
ROM `[bp+15]` looked like a dead byte and was not: it is the caller's DH,
and the original code read its top two bits as cylinder bits 11:10 (an
AMI ECHS convention). Writing there corrupted DX for every caller.

**4.4 The dispatch table.** Look for `shl di,1` then
`call [cs:di+<base>]`. Dump the table as words and label each handler
(AH=00 reset, 02 read, 03 write, 04 verify, 05 format, 08 get params,
09 init drive params, 0C seek, 10 test ready, 11 recalibrate, 14 diag,
15 get type, 19 park).

**4.5 The head-mask instruction — the key signature.** Search the ROM for
`80 E6 0F` (`AND DH,0Fh`). In a pure pass-through BIOS there is usually
exactly one, inside a small shared "select drive / build the 1F6h
drive+head byte" routine that almost every handler calls first. That one
instruction *is* the 4-bit head limit, and replacing it with a `CALL` to
your translation routine converts read, write, verify, format, seek and
long-read/write all at once.

Note how that routine is reached and what it has on the stack at that
point: ours pushed ES, BX, DX, CX before it, so at the mask instruction
the stack held `[ret][saved CX][saved DX]` — and the saved CX word is
popped back a few instructions later and used verbatim to program the task
file. **Overwriting that saved CX in place is how you inject the physical
cylinder/sector without touching any other code.**

**4.6 The AH=08h body.** Find the handler that builds CH/CL/DH from the
drive table: `dec dh` (heads-1), `sub ax,2`, `cmp ax,3FFh`, a `shl ah,6`
to pack cylinder bits into CL. Patch the *body* only — see §9.1 about its
tail.

**4.7 The cylinder-high port writes.** Search for `out` to 1F5h. Ours had
three copies (format, seek, and the shared read/write/verify path), each
of the form "take CL bits 7:6, OR in two bits salvaged from `[bp+15]`,
store to `[bp+06]`, out to 1F5h". Each one must be replaced with a plain
"load the full byte the translation routine computed and out it", or the
drive can still only reach 1024 physical cylinders.

**4.8 The drive parameter table (FDPT).** Handlers reach it through the
INT 41h / INT 46h vectors (`0000:0104` / `0000:0118`) — look for
`les bx,[es:bx]` after loading 104h/118h. Field layout (16 bytes):

```
+00 cylinders (word)  +02 heads (byte)  +05 precomp  +08 control
+0C landing zone (word)  +0E sectors (byte)
```

For a user-defined type (47) the table lives in RAM, and its address is a
CMOS setup option ("Hard Disk Type 47 RAM Area: 0:300"). Note where it is:
if it sits at `0:300` it is directly below the BIOS POST stack
(`SS:SP=0030:0100`, i.e. linear 0x300-0x400) and a deep patched call chain
could overwrite it. Measure your stack usage (§8) rather than hoping.

**4.9 Free space and the checksum.** Find a run of 00/FF bytes big enough
for your routines — ours was ~2 KB after the last message string. Then
find the checksum routine (a word-sum over the whole 64 KB that must come
out zero) and reserve one spare word in the free area to fix it up. An
even-aligned word, well clear of your code, keeps the arithmetic simple.

---

## 5. What to write

Three small routines, ~350 bytes total in our case:

**`calc_factor(RealCyl, RealHeads) -> TranslatedHeads`** — the ladder from
§2. Keep it pure 16-bit and document exactly which registers it
clobbers... then don't rely on the documentation (§9.2).

**`xlate_getparams()`** — replaces the AH=08h reporting body. Reads the
FDPT, calls `calc_factor`, computes the logical cylinder count, packs
CH/CL/DH, and leaves AL holding whatever the original left there. It must
*not* replace the handler's tail, which sets DL = number of drives, AH = 0
and the BDA status byte.

**`xlate_headcyl()`** — replaces `AND DH,0Fh`. Reads the FDPT and the
caller's original CX/DX off the stack, computes the LBA, converts to
physical CHS, then:

- returns the physical head in DH (the caller ORs it into the 1F6h byte),
- overwrites the **saved CX** on the stack with the physical
  cylinder-low/sector word,
- stores the physical **cylinder high byte** into a frame scratch slot
  that the port-write sites read (we used `[bp+06]`, the byte the original
  code used for the 1F5h value — confirm nothing else writes it),
- translates **only** for the functions that actually carry a CHS address
  (AH=02,03,04,05,0A,0B,0C). For everything else — reset, recalibrate,
  test-ready, init, park — reproduce the original behaviour byte for byte
  (`DH & 0Fh` plus the original cylinder-high formula). Those functions
  are called with garbage in CX and translating it produces garbage
  addresses and, worse, garbage returned to the caller.

---

## 6. The patch sites

For this ROM, five sites. The shape generalises:

| # | Site | Change |
|---|---|---|
| 1 | AH=08h reporting body | `CALL xlate_getparams` + `JMP` to the handler's own tail, NOPs to fill — **stopping short of the tail and the RET** |
| 2 | `AND DH,0Fh` in the shared drive-select routine | `CALL xlate_headcyl` (exactly 3 bytes, a perfect fit) |
| 3-5 | the three cylinder-high builders before `out 1F5h` | `mov al,[bp+06]`, NOPs to fill |

Rules that kept us out of trouble:

- Patch regions are replaced **in place**; never move code.
- Assert the exact original bytes of every region in the build script
  before overwriting, so a wrong address fails loudly instead of silently
  corrupting the ROM.
- Take the entry offsets of your routines from the assembler's listing
  file, not from hand-maintained constants (§9.1 is what happens when
  those two drift apart).
- Recompute the ROM checksum last, and assert it is zero.

---

## 7. Build script outline

```
1. read original ROM, assert size and MD5
2. assemble routines with nasm (ORG = free space address)
3. assert the original bytes at each patch site
4. splice routines into free space
5. write the patch bytes at each site (CALL/JMP computed from the listing)
6. zero the checksum filler word, sum the image, store the negation
7. assert the final word-sum is zero; write the output
```

---

## 8. Verification before touching hardware

Build a Unicorn-based harness that loads the real ROM at F000, sets up the
IVT (INT 13h, and 41h/46h pointing at a synthetic FDPT), the BDA
(`0040:0075` = number of drives), a stack, and a simulated IDE drive that
always reports ready/DRQ. Emulate software interrupts yourself (Unicorn
does not dispatch them), and fake `rep insw`/`rep outsw` (Unicorn hooks
IN/OUT but not the string forms). Log every task-file write with the
resulting LBA.

Then run these tests — in this order of value:

1. **A/B identity test.** Give the patched ROM a drive small enough that
   translation is the identity (factor 1). Call *every* INT 13h function
   and compare **everything** against the original ROM: AX, BX, CX, DX,
   SI, DI, BP, SP, ES, DS, the flags, the BDA status byte at 0040:0074,
   the full 32-bit register halves, and the exact sequence of IDE register
   writes. Anything that differs is a bug, full stop. This one test would
   have caught four of our six bugs on day one.
2. **LBA correctness.** For each geometry, read dozens of logical
   addresses (including 0/0/1, 0/1/1 and the last cylinder/head/sector)
   and assert the drive saw exactly
   `(cyl*heads_log + head)*sectors + sector - 1`, and never an address
   beyond the physical end of the disk.
3. **AH=08h contract.** CF=0, AH=0, DL = **number of drives** (not a
   leftover), correct CH/CL/DH, heads never 256.
4. **The POST sequence.** Whatever your POST does to the disk (ours:
   AH=14h diag, 09h init, 11h recalibrate, 08h, then 04h verify of the
   last cylinder) must return CF=0 for every geometry. A POST disk failure
   is often recorded in CMOS and can silently change later behaviour
   (§9.7).
5. **The ROM's own boot path.** Emulate INT 19h against a drive whose
   sector 0 carries an AA55 signature and confirm the ROM reads it,
   accepts it and jumps to 0000:7C00.
6. **Stack depth.** Track the SP low-water mark from the BIOS boot stack.
   Compare against the original (ours: 56 bytes original, 96 bytes
   patched) and against whatever sits below that stack.

---

## 9. Pitfalls — every one of these bit us

**9.1 Do not overwrite the handler's tail or its RET.** Our AH=08h patch
region was defined one byte too long and destroyed the handler's `RET`,
so every call fell through into the *next* handler's body (drive-select,
a ready-wait with a very long worst case, an INT 15h device-busy
notification). Symptom: **the machine froze during floppy boot whenever a
hard disk was configured.** Later, the region was still too long at the
other end and wiped the tail that loads `DL = number of drives`. Symptom:
**DOS reports "Invalid Drive Specification" and FDISK says "No fixed disks
present"** — and, maddeningly, it worked on exactly one drive, because DL
happened to receive a non-zero leftover from a division. Define patch
regions by disassembling the *exact* byte range and asserting it.

**9.2 Helper routines clobber registers.** `calc_factor` documented that
it destroyed DX; the caller read the cylinder count into DX, called it,
and used DX afterwards. Symptom: **every drive reported 1023 cylinders.**
Stash values on the stack across calls, or preserve them in the callee.

**9.3 Never write to the caller's saved registers in the INT 13h frame.**
`[bp+15]` is the caller's DH, not scratch. Symptom: **DH came back
corrupted from every read/write**, and after a reset (which runs the same
code with garbage in CX) DH came back as 0FFh — breaking any retry loop
that reuses DX, including the BIOS's own boot loader.

**9.4 PUSHA/POPA do not save the upper halves of 32-bit registers.** If
your math uses `movzx`/`imul`/`div` on EAX/ECX/EDX on a 386, use
PUSHAD/POPAD. Otherwise you silently zero the top 16 bits of three
registers for every caller. (On a 286 target, do the math in 16-bit
pieces instead.)

**9.5 Only translate the functions that carry an address.** Reset,
recalibrate, test-ready and park are called with whatever happened to be
in CX. Translating that produces nonsense; passing the nonsense back to
the caller produces bugs that look random.

**9.6 The head count must never reach 256.** See §2. Symptom: correct
capacity in FDISK, then "Error writing fixed disk", or a hang.

**9.7 Know what your POST does with a disk failure.** Ours sets bit 3 of
CMOS register 0Eh when its disk init fails, and the boot code checks that
bit and **silently skips booting from C:**, falling back to the floppy
with no message. If "I can only boot from A:" appears, check that bit
before suspecting the translation.

**9.8 Do not change the geometry of drives that already work.** When you
change how the logical geometry is computed, verify that every previously
working geometry maps to exactly the same numbers, or existing partitions
become unreadable. (Our 255-head change was written specifically to be
arithmetically identical for all the power-of-two cases.)

**9.9 Non-BIOS causes exist.** Marginal CF cards and cheap CF-to-IDE
adapters produce failures that look exactly like translation bugs.
Once the emulator says the ROM is correct for a geometry, suspect the
hardware: try another card/adapter before rewriting code.

**9.10 Leftovers on the media.** A card from an appliance may carry a
foreign boot record; a partition may not be marked Active. Both look like
"can't boot from C:".

---

## 10. Hardware test protocol

In this order, because each step depends on the one before:

1. POST completes with no disk error message; the setup screen shows the
   drive's real geometry (auto-detect writes the *physical* values).
2. Boot from floppy with the disk configured — checks that nothing hangs.
3. `FDISK` sees the drive, reports the expected size, writes a partition,
   and the partition is marked **Active**.
4. `FORMAT C: /S` completes and reports the expected free space.
5. `DIR C:`, copy files, then ScanDisk — exercises reads and writes
   across the whole surface.
6. Boot from C: with the boot order set to C:, A:.
7. A benchmark or surface test that walks every cylinder (we used the
   linear read/verify test of a DOS benchmark) — this is what proves the
   high cylinder bytes are right at the far end of the disk.

Keep a written table of drive, physical CHS, translated CHS and result.
The pattern across geometries is what identifies a bug; a single failing
drive rarely does.

---

## 11. Limits of this approach

- Ceiling is 1024 x 255 x 63 x 512 = **7.84 GiB** (8.42 GB). Beyond that needs INT
  13h extensions (AH=41h/42h) and an OS that uses them.
- FAT16 caps a partition at 2 GB; use multiple partitions, or a DOS with
  FAT32 (MS-DOS 7.1 / Win9x) as we did for the 8 GB card.
- Some ROMs also carry a boot-sector write-protection ("virus warning")
  check that fires on writes to cylinder 0/head 0/sector 1 and waits for a
  keypress. It is usually a CMOS option; if partitioning fails with no
  obvious cause, turn it off before blaming the patch.
- The drive's *physical* geometry must be entered in setup (type 47).
  Translation is computed from it at every call, so the CMOS values and
  the on-disk partition table must stay consistent — changing the setup
  geometry later invalidates existing partitions.

---

## 12. This ROM, for reference

AMI BIOS 11/11/92, OPTi 82C495SLC, 64 KB at F000. Original MD5
`e80a824d8f3c39d40b98a4be5d8d9cff`.

| Item | Address |
|---|---|
| INT 13h entry | `0xA3E7` |
| Dispatch table (AH*2) | `0xA44B` |
| AH=08h body patched | `0xA659-0xA685` (tail `0xA686-0xA690` untouched) |
| `AND DH,0Fh` -> `CALL xlate_headcyl` | `0xA965` |
| Cylinder-high sites | `0xA60E`, `0xA7EA`, `0xAB3D` |
| Shared "write CHS to task file" | `0xAB14` |
| Boot-sector write protection | `0xAB62` (CMOS option) |
| POST disk init / verify | `0xAD66-0xAE09` |
| ROM boot path (INT 19h) | `0xF758`; MBR read helper `0xF810` |
| FDPT vectors | `0000:0104`, `0000:0118`; type 47 RAM area `0000:0300` |
| Free space used | `0x76F5` (+ checksum filler word at `0x7900`) |
| POST/boot stack | `SS:SP = 0030:0100` (linear 0x300-0x400) |

Final result (v5, the version in this repository): 8 GB CF (16000/16/63) -> 1002 cyl / 255 heads / 63
sectors, 7.67 GB usable, FDISK + FORMAT + boot from C: all working.

**Files in this project:** `asm/xlate.asm` (the three routines),
`py/build.py` (assemble + splice + patch + checksum), `py/ide_harness.py`
(emulator), `py/regress.py` and `py/boot_test.py` (the tests in §8),
`py/verify_model.py` (the translation math in plain Python),
`HOW_THE_PATCH_WORKS.md` (walk-through of every changed byte),
`BIOS_ADDRESS_MAP.md` (the map from §4), `BIOS_Evaluation.md` (the full
bug history with the diagnosis of each).
