# Modding a Legacy BIOS: LBA, POST Screen and Setup

**What this documents:** the method behind BUBios, which turned a 1992 AMI
386 BIOS (SOYO SY-019L1, OPTi 82C495SLC, 64 KB ROM) into a BIOS with LBA
up to 128 GB, automatic IDE detection, a full-screen POST display with a
boot countdown, and a new setup program, all inside the original 64 KB
EPROM. Everything was confirmed on the real board.

**Read first:** [`CHS_TRANSLATION_PORTING_GUIDE.md`](CHS_TRANSLATION_PORTING_GUIDE.md).
It covers the ECHS translation this work is built on, how to map an
unknown ROM (§4), and the emulator approach (§8). This guide assumes you
have done that mapping. Paths are relative to the repository root.

**Where the details are:** [`BUBios/README.md`](../BUBios/README.md) (what
each feature does and every hook), [`BUBios/CHANGELOG.md`](../BUBios/CHANGELOG.md)
(every bug and how it was found), [`BIOS_ADDRESS_MAP.md`](BIOS_ADDRESS_MAP.md) §9
(every address), and the sources `BUBios/asm/bubios.asm` (setup) and
`BUBios/asm/post.asm` (POST, INT 13h extensions).

The addresses below are for this ROM. Other AMI BIOSes of the same era
share much of the structure, but every address has to be found again.

---

## 1. Ground rules

These rules kept a ROM with thousands of changed bytes working on real
hardware.

- **Never move original code.** Every hook is a same-length `CALL` or
  `JMP` over an instruction or a short sequence, padded with `NOP`s. No
  other jump or call in the ROM needs fixing.
- **Hook, don't rewrite.** Replace the places where the BIOS *prints*
  something, or wrap a routine (call the original from your hook). Leave
  the order of POST and what POST tests unchanged. BUBios compares the
  POST checkpoint sequence (port 80h) of its POST screen build with the
  version before it, and they are identical.
- **The build script asserts everything.** Before it writes anything,
  `BUBios/py/build_bubios.py` checks:
  - the MD5 of the input ROM
  - the original bytes at every patch site
  - every AMI string and option record it reuses, by content
  - the MD5 of every dead-code range before clearing it, and of every
    kept neighbour after patching
  - that every free block is empty in the input and that the new code
    fits
  - the final checksum (16-bit word sum of the 64 KB image = 0)

  A wrong address then fails the build instead of producing a ROM that
  crashes.
- **Take entry points from the assembler's output.** The build reads
  symbol addresses from the NASM map file, so a call site can never point
  at a stale offset.
- **Build on a confirmed ROM.** BUBios starts from the
  hardware-confirmed ECHS ROM and checks its MD5. Keep the disk code
  byte-identical and rerun its regression suite (`regress_echs.py`) on
  every build.
- **No recovery block.** Keep the original chip or dump, and a spare
  EPROM.

## 2. Finding room

A 64 KB ROM of this era has only a few KB of free space. BUBios used all
of it and then reclaimed 4.5 KB of dead AMI code. At the end, about 10
bytes were left; turning a repeated sequence in its own drawing code into
a helper later gave back 21 bytes, enough for one more small feature.

### 2.1 Free blocks and where code may run

Look for runs of `00` or `FF`. Then check *how* each block is reached,
because not every free byte can hold every kind of content:

- **The setup program runs from a RAM copy.** This AMI setup copies
  `F000:0000-DFFF` to segment `0100h` and runs with `DS = SS = 0100h`.
  Anything the setup reads through DS (strings, tables) must lie below
  `E000`, and not in `DA84-DFFF`, which is the setup's stack in that RAM
  copy. Code in `DA84-DFFF` still runs fine from `F000`. BUBios therefore
  splits the setup into a code block (`DA84`) and a data block (`7902`).
- **POST code runs from `F000`** with `DS = 0040h` (the BDA). Strings
  shared with the setup can be read with a CS override.
- **Reserve one word for the checksum** in a free block and fill it last.

### 2.2 Reclaiming dead code

A routine that nothing can reach anymore is free space, if you can prove
that nothing reaches it. BUBios used three independent checks:

1. **Recursive disassembly** from the routine's entry point and its
   internal jump tables, and from every live entry point of the ROM.
2. **Reference search.** Check every `CALL` and `JMP` in the ROM, and
   every `push imm16`, for targets inside the range.
3. **Execution check.** Run all test suites with a watch on the range.

Then:

- Clear the range in the build, but only after asserting its MD5.
- Keep the small shared routines that sit between dead ones (here a CR/LF
  routine, the drive-information display that setup auto-detect uses, an
  error dialog and a block of texts), and assert their MD5 after
  patching.
- Point any leftover table entry at a `RET` instead of the freed space
  (the old main-menu table still named the Hard Disk Utility).

Good candidates are features your change makes unreachable: the
configuration box your new POST screen replaces, a text your hook no
longer prints (`WAIT......`), the bytes your hook jumps over (40 bytes of
the old memory-count print), and dangerous tools you remove from the menu
(the low-level-format Hard Disk Utility, meaningless on IDE and CF).

## 3. Hooking POST

### 3.1 Find the print sites

AMI's POST prints through a few routines, usually `MOV SI,<string>` /
`CALL <print>`. Each such pair is a hook site of exactly the right size.
Some are reached twice: POST prints the logo, the BIOS ID lines and
`WAIT......` again after an in-POST setup visit (`12BF`, `12C6`, `12CD`
here, next to `593B`, `5945`, `4CB4`). Hook every copy.

When the replaced routine does more than print, call it from your hook.
AMI's BIOS ID routine (`F42E`) also sets the A20 gate, so `bu_post_init`
still calls it.

### 3.2 The environment early in POST

- **Interrupts are off, and on a cold start the interrupt controllers are
  not set up yet.** Never execute `STI` there. Save and restore the flag
  (`PUSHF` / `CLI` … `POPF`). An `STI` hidden in the clock measurement
  caused occasional hangs at power-on or RESET, which were first blamed
  on the coprocessor probe.
- **AMI clears the BDA** before the first hook. BUBios keeps its state in
  the BDA inter-application area `40:F0-40:FF` and clears it before the
  boot.
- **The memory test runs in protected mode** and resets the CPU back to
  real mode for every 64 KB block it reports. Your memory-display hook
  runs after such a reset: pass what you need in registers (here AX =
  blocks counted, DX = blocks left, BP = the pass) or keep it in the BDA.
- **Some values do not exist yet.** AMI sizes and enables the cache last,
  and the coprocessor probe needs AMI's IRQ 13 handler. Show a
  placeholder (`...`) and fill the field at the end of POST.
- **The CMOS may describe old hardware.** After a RAM upgrade, the
  memory size in CMOS is from the previous boot. Wait for the value AMI
  measures.

### 3.3 Timing and measuring

- **Delays independent of the CPU clock:** port 61h bit 4 toggles with the
  DRAM refresh (every 15.09 µs). The countdown and the 2-second pause for
  the video card's sign-on count those toggles.
- **Measuring the CPU clock:** time a loop of `DIV` + `LOOP` with PIT
  channel 2 and divide by the known cycle counts per CPU family. Early in
  POST the cache is still off and code fetches from the ROM are slow, so
  BUBios copies the loop to RAM (`0:7C00`), uses 32 `DIV`s per pass so
  that fetches hardly matter, accepts a reading only if it is within 1/16
  of a standard clock, and otherwise tries again later.

### 3.4 Tamper checks

AMI's POST checks that `(C) American Megatrends Inc.,` is on screen at row
21 and crashes on purpose if it is not (routine `F3DB`, checkpoints 40h,
60h, 95h). It only runs with an invalid CMOS, so it shows up only in the
cleared-CMOS test. A new POST screen either keeps the line in place or
disables the check (a `RET` at its entry). Leave the ROM's integrity
checksum alone.

### 3.5 Setup visits, restarts and the countdown

- **AMI's DEL flag** is CMOS 0Eh bit 0, set by the keyboard poll during
  the memory test. A DEL pressed after that poll is lost unless your own
  code checks for it. BUBios checks the key and AMI's flag, and clears
  the flag, so DEL works from the memory test to the end of the countdown.
- **Detecting changes made in setup:** compare AMI's CMOS checksums
  (10h-2Dh and 34h-6Eh) before and after, *plus* any CMOS bytes of your
  own that lie outside them.
- **Applying changes:** restart with a keyboard-controller reset and CMOS
  shutdown code 0. Here AMI's chipset setup (checkpoint 05h) then turns
  the shadow RAM off again, so the EPROM is checksummed afresh.
- **After an in-POST setup visit,** POST carries on. Forget any cached
  state (such as "drives already identified") and redraw.
- **Hand over a clean screen:** set the video mode again just before the
  boot, so DOS starts in normal colours with the cursor at the top.

## 4. IDE work during POST

- **Select the master again after IDENTIFY.** If a missing slave stays
  selected, AMI's own disk setup (checkpoint 91h) hangs.
- **A drive is absent only if nothing answers at all** (status FFh, 00h
  or 7Fh: a floating bus), and only after the memory test. Before that, a
  drive may still be spinning up; it answers "busy". BUBios waits up to
  10 s for drives that are not ready.
- **Drop configured drives that are absent.** AMI waits about a minute
  for them and then stops with *HDD controller failure*. Setting their
  CMOS type to "not installed" (and fixing the checksum) before AMI's disk
  setup avoids that.
- **Auto-detect** writes the drive's own geometry as type 47 into CMOS,
  fixes the checksum, and does it before AMI sets up the disks, so the
  drive works in the same boot.
- **Your own CMOS settings** go outside AMI's checksummed ranges, with a
  check byte (here CMOS 7Fh, check byte 7Eh = 7Fh xor A5h), so a cleared
  or random CMOS reads as "off".

## 5. Replacing the setup program

AMI's setup is table-driven, and every page is drawn through one header
routine. BUBios replaced only two routines, the main menu and that
header, and kept AMI's Standard, Advanced and Chipset pages, the
password, auto-detect, defaults and the Y/N dialogs running unchanged.

- **Reuse AMI's option records** to display values (the Summary page
  prints them), so the text always matches the Advanced page. The build
  checks each record by its label.
- **Add keys at the getkey calls** of AMI's editors (Tab / Shift-Tab,
  digits for the date and time), instead of rewriting the editors.
- **Go through AMI's own routines** for anything with side effects. The
  typed date is committed through AMI's set-date routine, which keeps the
  day valid for the month. Pause AMI's once-a-second clock redraw while
  the user types.
- **Colours:** the scheme table is patched in place; F2/F3 and the CMOS
  byte that stores the choice stay AMI's.
- **Footer texts** are rewritten in place and must not grow.
- **Probe hardware directly** rather than trusting BDA flags that POST
  sets later. The coprocessor flag in the BDA is only set after setup has
  run; BUBios probes with `FNINIT` instead.
- **Remove dangerous menu entries** (low-level format) rather than
  leaving them one keystroke away.
- **Watch 16-bit sums:** extended + base memory with 64 MB is
  64512 + 1024 = 65536 KB, which wraps to 0 in 16 bits.

## 6. Adding LBA: the INT 13h extensions

This is how BUBios went past the 8.4 GB CHS ceiling that the ECHS patch
reaches.

1. **Hook the INT 13h entry.** Replace the first instructions of the hard
   disk handler (here `CMP DL,80h / STI / CLD` at `A3E7`) with a `JMP` to
   your code. Route AH=41h-48h for hard disks (DL >= 80h) to the
   extensions; re-execute the replaced instructions and jump back into
   the original code (`A3EC`) for everything else.
2. **Implement the EDD 1.1 "fixed disk access subset":**

   | AH | Function |
   |---|---|
   | 41h | Installation check: BX = AA55h in, BX = AA55h, AH = 21h, CX = 1 out |
   | 42h / 43h | Read / write from a disk address packet: up to 127 sectors, 64-bit LBA field, 28 bits used (128 GB) |
   | 44h | Verify |
   | 47h | Seek: accept, do nothing (IDE drives seek on their own) |
   | 48h | Drive parameters: physical geometry and total sectors (IDENTIFY words 60-61) |

3. **Use CHS addressing for requests inside the CHS geometry**, and LBA
   addressing only beyond it. Drives without LBA then work too, and CF
   cards that report their full size as CHS (31045/16/63 on a 16 GB card,
   legal in ATA) are reached entirely through CHS.
4. **Leave the classic functions alone.** AH=02h, 03h, 08h and the ECHS
   translation stay byte-identical, so existing partitions and
   installations are unaffected. Prove it with the ECHS regression.
5. **Report errors properly:** a request past the end of the disk, more
   than 127 sectors, a missing drive.

A bug to avoid: the first 42h/43h build advanced the transfer buffer twice
per sector. The emulator test that reads back what was written found it.

Test in three layers: calls in the POST emulator after a full POST
(`BUBios/py/int13x.py`), a boot sector that calls every function and
prints the results (`BUBios/asm/lba_test_mbr.asm`, run in 86Box on a
19.6 GB disk image, where the sector written through 43h was found at the
expected LBA), and then an LBA-aware OS on the real board (MS-DOS 7.1 /
Windows 95 B with a 20 GB and an 80 GB disk).

## 7. Hardware quirks that look like BIOS bugs

- **CF cards and the floppy change line.** Port 3F7h is shared: the
  floppy controller supplies bit 7 (disk change), and the IDE side
  decodes the same address. ATA says a drive must leave bit 7 alone; CF
  cards drive it, so MS-DOS never sees a disk change. BUBios recognises a
  CF card from IDENTIFY (word 83 bit 2, or word 0 = 848Ah) and makes INT
  13h AH=15h report "no change line" for the floppy, so DOS falls back to
  its own volume-serial check.
- **Measure before you change code.** A 408-byte DOS tool
  (`BUBios/tools/DSKCHG.COM`) that shows AH=15h/16h and the raw port live
  proved the cause on the board. The emulator had shown that the BIOS
  answered exactly like the original ROM.
- **Marginal CF cards and adapters** exist (see the CHS guide §9.9).

## 8. Emulation and testing

Several BUBios bugs were found in emulation before a chip was burned
(the slave-select hang, the clobbered clock value, the double buffer
advance), and the bugs found on the board got new emulator checks. The
tools, in order of how much of the ROM they run:

| Tool | Runs | Notes |
|---|---|---|
| `py/ide_harness.py` (and the copy in `BUBios/py/`) | single INT 13h calls through the real dispatcher | the ECHS workhorse; see the CHS guide §8 |
| `BUBios/py/setup_emu.py` | the ROM's own setup program, entered the way POST enters it (`F000:2968`) | emulates INT 10h / 16h, CMOS, OPTi ports, PIT channel 2; saves the screen as PNG |
| `BUBios/py/post_emu.py` | the complete POST from the reset vector to INT 19h | PICs with real interrupt delivery, PIT, 8042 (including the CPU reset AMI uses to leave protected mode), DMA, RTC/CMOS with both checksums, OPTi ports, floppy controller, two IDE drives with CHS and LBA, serial/parallel ports, colour or mono video |
| `BUBios/py/int13x.py` | INT 13h calls on the machine after POST | used for the extensions |
| 86Box | the ROM on an emulated machine with the same chipset | *[OPTi 495] DataExpert OPTi-495SX*, with its ROM file replaced; real video BIOS, real timing |

Unicorn does not dispatch software interrupts and has no hooks for
string I/O (`REP INSW` / `OUTSW`); the harnesses handle both themselves.

Checks worth copying (all in `BUBios/py/test_post.py` and
`test_bubios.py`):

- the POST checkpoint sequence is identical to the version before your
  POST changes
- the interrupt flag is still off after the POST screen's early work
- memory sizes from 4 to 64 MB, and a RAM upgrade with a stale CMOS size
- a cleared CMOS (errors, F1, setup, boot)
- master only, master and slave, no disk, auto-detect with missing and
  with wrong CMOS entries
- setup visits during POST and during the countdown, with and without
  saved changes
- colour and monochrome video
- the INT 13h extensions above 8.4 GB, and their error cases
- the classic INT 13h functions, unchanged

The full POST suite takes 25-35 minutes. Stray 86Box processes slow it
down a lot.

## 9. Pitfalls: every one of these happened

Each item is a bug BUBios had; [`BUBios/CHANGELOG.md`](../BUBios/CHANGELOG.md)
describes each one in its build.

1. **`STI` in early POST** → occasional hang at power-on or RESET.
2. **A missing slave left selected after IDENTIFY** → AMI's disk setup
   hangs at checkpoint 91h.
3. **A configured drive that is not connected** → AMI waits a minute,
   then *HDD controller failure / Press F1*; on the board it looked like
   a freeze.
4. **16-bit memory total** → "Total Memory 0K" with 64 MB.
5. **Stale CMOS memory size** → the memory bar jumped after a RAM
   upgrade.
6. **A register overwritten between two checksum reads** → setup changes
   in one CMOS range did not trigger the restart.
7. **Own CMOS bytes outside the checksums** left out of the change
   detection → switching auto-detect on in setup had no effect.
8. **DEL pressed after AMI's own poll** → lost, or taken as "any other
   key" and the machine booted.
9. **Cached state across an in-POST setup visit** → drive names missing
   after setup.
10. **A value printed from a clobbered register** → 284.8 MHz.
11. **A value shown before the BIOS set it up** → cache briefly shown as
    Disabled.
12. **The transfer buffer advanced twice per sector** in the extended
    read/write.
13. **AMI's copyright tamper check** → a crash that only appears with a
    cleared CMOS.
14. **A BDA flag that POST sets only after setup** → coprocessor shown as
    "None".
15. **A CF card driving port 3F7h bit 7** → floppy swaps not noticed by
    MS-DOS.

## 10. Checklist for another BIOS

1. Dump the ROM, record its MD5, keep a spare EPROM.
2. Map the ROM ([CHS guide §4](CHS_TRANSLATION_PORTING_GUIDE.md)): reset
   vector, checksum, INT 13h, free space.
3. Build the emulator harnesses first: INT 13h calls, then the setup,
   then the full POST. Record the original ROM's POST checkpoint sequence
   and screens as the reference.
4. If the BIOS has the 504 MB limit, do the ECHS patch first and confirm
   it on hardware.
5. Map the POST print sites, the setup's menu and header routines, and
   the INT 13h entry. Note which code runs from a RAM copy.
6. Find dead code and prove it dead (§2.2) before you need the space.
7. Write the build script with assertions for every site, string, record
   and range (§1).
8. Add features one at a time; after each, rerun every suite and compare
   the checkpoint sequence with the original.
9. Test on real hardware in the order of the CHS guide §10, then the
   features: POST screen, countdown and setup visits, auto-detect,
   absent drives, LBA with an LBA-aware OS.
10. Keep a changelog per build with the ROM's MD5 and what the board
    showed.
