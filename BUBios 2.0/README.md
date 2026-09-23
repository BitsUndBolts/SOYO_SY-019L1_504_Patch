# BUBios 2.0

BUBios 2.0 adds a new **POST screen** to the BUBios setup and removes dead AMI code from the ROM to make room for it. Everything from BUBios 1.0 (the MR-BIOS-style setup) and from the parent project (ECHS large-disk support up to 8.4 GB) is still there and unchanged.

**Status:** version 2.0, verified in two emulators (see *Testing*). It has not yet run on the real board. This BIOS generation has no recovery block, so keep the 1.0 chip or the original dump before programming an EPROM.

| | |
|---|---|
| ROM image | `binary/BUBIOS2_SY019L1.BIN` (64 KB, 27C512) |
| MD5 | `52eea91efbd108f8fff972c79e31f875` |
| Built from | `binary/base/SY019L1_27C512_CHSPATCH.BIN` (ECHS v5, MD5 `8e520e91100f932d3f054883b08d37da`) |
| Setup | BUBios 1.0 setup, version text changed to 2.0 |

![POST screen, ready to boot](shots/post_2_ready.png)

## The POST screen

The screen is drawn as soon as the video BIOS is up. After that, POST fills it in as it runs:

| Area | Contents | Filled in |
|---|---|---|
| Title bar | BUBios 2.0, Power-On Self Test | at once |
| Processor | 80386 / 80486 / CPUID family; Cyrix 486DLC-class cores are named as such | at once |
| Coprocessor | 387 present / On-chip / None (FNINIT probe) | at once |
| Clock | measured CPU clock, same method as the setup's Summary page | end of POST |
| Cache | external cache size as the OPTi chipset reports it, or Disabled | end of POST |
| Shadow RAM | Video + System / Video / System / Disabled | end of POST |
| RTC battery | OK / Low (CMOS register 0Dh) | end of POST |
| Memory | progress bar and count in KB, updated for every 64 KB block AMI tests | live |
| Memory map | one cell per MB: counted, being counted, still to come. Above 32 MB one cell stands for 2, 4 … MB, and the label says so | live |
| Floppy A:/B: | drive types from CMOS | at once |
| Disk C:/D: | **model name read from the drive (IDENTIFY)**, drive type, physical geometry, the geometry DOS sees through ECHS, and size in MB | geometry at once, name at end of POST |
| Ports | COM1-4 and LPT1-3 with their I/O addresses | end of POST |
| Option ROMs | every ROM found between C000 and EFFF, with its size | end of POST |
| Message rows | AMI's error messages, in red, and the boot messages later | when needed |
| Status bar | what POST is doing: *Testing memory*, *Press DEL to run Setup*, *Checking devices*, *POST found a problem*, and at the end the boot order and how long POST took. On the right, the AMI BIOS ID string | live |

The end-of-POST beep is replaced by a two-note chime. POST itself is not changed. The sequence of POST checkpoint codes is identical to BUBios 1.0, and so are the DEL key, the F1 prompt and the error messages.

The screen uses the colour scheme selected in setup (F2/F3): teal, amber, green, classic blue, grey or AMI. On a monochrome adapter it uses mono attributes.

| | |
|---|---|
| ![Memory test](shots/post_1_memory_test.png) | ![Errors](shots/post_3_errors.png) |
| Memory test in progress | Error messages (a cleared CMOS, blue scheme) |
| ![Amber](shots/post_4_amber.png) | ![Monochrome](shots/post_5_mono.png) |
| Amber scheme | Monochrome (MDA) |

*The POST screenshots were taken in 86Box on its OPTi 495 machine with this ROM installed. That is why the disk is called "86B_HD00" and why POST takes about 15 s there.*

The setup pages are the same as in 1.0, apart from the version number; their screenshots are in `shots/` as well (`1_summary.png` … `9c_colour_scheme_blue.png`).

## Dead code removed

Four pieces of AMI code can no longer be reached. Their space was cleared (4,501 bytes in total), and the POST screen now uses part of it:

| Range | Size | What it was | Why it is dead |
|---|---|---|---|
| `39C0-3B83`, `3B8F-4033` | 1,641 B | AMI's "System Configuration" box, including its texts | replaced by the POST screen |
| `4034-4167`, `429D-49DA` | 2,162 B | Hard Disk Utility: low-level format, auto interleave, media analysis, bad-track editor | not in the menu since BUBios 1.0; format and interleave are meaningless on IDE and CF |
| `3292-3481` | 496 B | Hard Disk Utility texts | as above |
| `19B0-1A79` | 202 B | cache and CPU-clock lines printed under the configuration box | only called by the box |

The following routines lie between or beside those ranges and are kept:

- the CR/LF routine at `3B84-3B8E`
- the drive-information display at `4168-429C`, used by auto-detect
- the error dialog at `49DB-4A43`
- the error and auto-detect texts at `3482-36D3`

The last entry of AMI's old main-menu handler table (`2ECE`) pointed at the Hard Disk Utility. It now points at a RET.

The dead code was found in three steps:

1. **Recursive disassembly.** Starting from the utility's entry point and from its two internal jump tables (`332F`, `33B3`) marks everything the utility can reach. Starting from the live entry points marks everything live code can reach.
2. **Reference search.** Every CALL and JMP in the ROM was checked for references into the ranges from outside them.
3. **Execution check.** The full setup test suite and the POST tests were run with a watch on the removed ranges. No code executed there.

The build script checks the MD5 of every removed range before clearing it, and the MD5 of every kept neighbour after patching.

**Space after 2.0:** 209 B (`39C0` block), 272 B (`3B8F` block), 370 B (`429D` block), plus the cleared 496 B at `3292` and 202 B at `19B0`, and the 198 B left in the 1.0 blocks.

## How it works (short version)

AMI's POST prints through a handful of routines. BUBios 2.0 replaces those prints or wraps them. It never changes the order of POST or what POST tests.

| Site | Original | Now |
|---|---|---|
| `593B`, `12BF` | logo line `AMIBIOS (C)1992 …` | nothing |
| `5945`, `12C6` | `CALL F42E` (BIOS ID lines) | `bu_post_init`: calls F42E first (it also sets the A20 gate), then draws the screen |
| `4F04` | "Hit <DEL>, If you want to run SETUP" | `bu_delmsg`: status line |
| `4EA1` | memory count `nnnnnn KB OK` (49 bytes) | `bu_memshow`: bar, map and count |
| `4CB4`, `12CD` | `WAIT......` | `bu_wait`: status line |
| `120D`, `158C` | AMI's error list | `bu_errors`: clears the message rows, then AMI's list |
| `1610` | end-of-POST beep | `bu_chime` |
| `1613` | clear screen before the configuration box | `bu_refresh`: same mode reset, then the screen is redrawn at once |
| `164C` | configuration box | `bu_final`: clock, cache, shadow, IDENTIFY, ports, ROMs, boot order, POST time |

Notes for anyone changing this code:

- **AMI checks its copyright line on screen.** At POST checkpoints 40h, 60h and 95h, routine `F3DB` reads row 21, columns 0-28. If the text there is not `(C) American Megatrends Inc.,`, POST jumps into a deliberately unbalanced RET and crashes. With a valid CMOS the check is skipped, which is why this only showed up with a cleared CMOS. The POST screen therefore always keeps that line at row 21. Only `bu_errors` clears it, because nothing checks it after that point.
- **The memory count runs after a CPU reset.** AMI tests memory in protected mode. For every 64 KB block it resets the CPU back to real mode to print the count. `bu_memshow` is called at that point with AX = blocks counted and DX = blocks left. BP = 1, 0Ah or 10h tells it which pass is running.
- **State between the hooks** is kept in the BDA inter-application area `40:F0-40:FF`, which nothing else uses during POST. `bu_final` clears it before the boot.
- **The clock** is measured with the same loop and constants as the setup's Summary page. The loop is copied to `0:7C00` so that it runs from RAM. The IDENTIFY data is read into `0:7E00`. INT 19h overwrites both afterwards.

## Build and test

Run these from this folder. You need NASM, Python 3 and `pip install unicorn pillow`. This folder has everything else, including copies of the base ROMs and the ECHS test scripts.

```
python3 py/build_bubios.py     # builds binary/BUBIOS2_SY019L1.BIN from binary/base/
python3 py/test_bubios.py      # 67 checks of the setup (unchanged from 1.0)
python3 py/test_post.py        # 40 checks of the POST screen (about 2 minutes)
python3 py/regress_echs.py     # ECHS INT 13h regression + INT 19h boot test
python3 py/shots.py            # setup screenshots into shots/
```

- **`post_emu.py`** is a new emulator that runs the complete POST from the reset vector. It uses Unicorn and models the following:
  - both 8259 PICs with real interrupt delivery
  - the 8254 PIT against a virtual clock
  - the 8042 keyboard controller, including the CPU reset AMI uses to leave protected mode
  - the DMA controllers and the RTC/CMOS
  - the OPTi index ports
  - the floppy controller
  - master and slave IDE drives with IDENTIFY data
  - a colour VGA or a monochrome adapter

  It is also a good tool for any further work on this ROM.
- **`test_post.py`** checks the screen in the normal case, the live memory display, and memory sizes of 4, 32, 48 and 64 MB. It also checks:
  - no disk, a master plus a slave, and a small ROM-table drive
  - a cleared CMOS, including F1 into setup, quitting setup and booting, the colour schemes and monochrome
  - the chime
  - that the POST checkpoint sequence is identical to BUBios 1.0
- **86Box** (v6.0, machine *[OPTi 495] DataExpert OPTi-495SX*, with `roms/machines/ami495/opt495sx.ami` replaced by this ROM) ran the full POST on an emulated 386DX-40 with a 387, a Tseng ET4000 or MDA card and an IDE disk. The screenshots above come from it. 86Box also confirms the processor and clock (80386, 40.0 MHz), which Unicorn cannot show.

## Known limits

- The memory map shows progress, not faults. On a memory error, AMI stops with its own message, as before.
- The POST time uses the RTC, so its resolution is one second. It is not shown after a visit to setup (it would include the time spent there) or when the RTC gives an implausible value.
- The clock constants are calibrated for Intel/AMD 386 and 486 cores. A Cyrix 486DLC-class CPU is detected and named, but its clock reading may be off until it is calibrated on a real chip.
- Option ROMs that print during POST (for example a network boot ROM) print wherever the cursor is.
