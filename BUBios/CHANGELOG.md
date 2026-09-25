# BUBios changelog

BUBios grew in three steps, all in this one project: 0.1 and 1.0 replaced the setup program, 2.0 added the POST screen, and 2.1 added the boot countdown, disk auto-detection and LBA. Only the current release ROM is kept in `binary/`; the ROMs of earlier versions are in the git history.

## 2.1, eighth build — 2026-09-25

ROM `binary/BUBIOS2_SY019L1.BIN`, MD5 `d5894024e3665d4a426e5b7cdb47a87c`

- **A " CF " badge on the POST screen for a CompactFlash card.** When IDENTIFY shows that a drive is a CF card (word 83 bit 2, or word 0 = 848Ah, the same test as in the seventh build), its model row gets a four-cell badge in the title-bar colours between the label and the model name: `Disk C:  CF  SanDisk SDCFB-8192`. It is drawn by `detect_disks` at the moment the card is recognised, so it also says that the floppy change line will be reported as "not available" (40:8F bit 3, set by `bu_final`). A hard disk gets no badge. On a monochrome adapter the badge is inverse video.
- **Room for it:** `fill_values` cleared a field and set the value attribute with the same four instructions five times; this is now one helper, `clear_v` (clear, `goto`, `value_attr`). The screen output is identical: under the POST emulator the old and new ROM produce the same POST checkpoint sequence and the same screen at every checkpoint, apart from the badge (hard disk, CF master, CF slave, mono without disks, auto-detect with the blue scheme, cleared CMOS). The new code costs 18 bytes; the helper saves 21. About 13 bytes are left.
- `build_bubios.py` runs NASM with relative file names, so `asm/bubios.map` no longer records the path of the machine it was built on.
- `test_post.py`: 3 new checks, 97 in total (badge on the CF master's row, on the CF slave's row and not on the hard disk's, in the title colours; no badge with a hard disk only).

## 2.1, seventh build (release) — 2026-09-25

ROM MD5 `b975393064d7b8a387dd7bf6e5d32286` (the release confirmed on the board; replaced by the eighth build, which only adds the CF badge)

**Confirmed on the board**:
- the floppy directory updates after a swap with a CF card attached
- unplugged drives are dropped from setup without stalling POST
- *Entering Setup* appears after DEL

All reported bugs are closed.

- **Floppy swaps noticed with a CF card on the IDE bus.** A CF card drives bit 7 of port 3F7h and hides the floppy change line, so MS-DOS kept showing the old directory.
  - BUBios recognises a CF card from IDENTIFY (word 83 bit 2, or word 0 = 848Ah). `ide_id` now also keeps words 83 and 0.
  - At the end of POST it sets bit 3 of 40:8Fh.
  - INT 13h AH=15h for floppies then reports "no change line" (AH=01): AMI's call to its stub at D385 now goes to `cf_chgline`. DOS falls back to its own check (volume serial after 2 s).
  - Hard disks only: unchanged.
- **Confirmed on the board:** no more hangs at the video sign-on (fifth build fix).
- **Room:** AMI's unused "WAIT......" text at 76AC (13 bytes) is a new block (`section wait`); some strings moved. About 10 bytes are left.
- `test_post.py`: 94 checks. It now also checks change-line reporting with a hard disk, a CF master and a CF slave.

## 2.1, sixth build — 2026-09-24

ROM MD5 `db32ec060dba87c8ff9d6cb162ce5d72` (replaced by the seventh build)

- **A drive set up in BIOS but not connected no longer stalls POST.** AMI waited about a minute at checkpoint 91h for it, then stopped with *HDD controller failure / Press F1*. On the board this looked like a freeze at *Checking devices*.
  - When nothing answers on the IDE bus for that drive after the memory test, BUBios now sets its CMOS type to "not installed" and fixes the checksum. This happens with auto-detect on or off, before AMI's disk setup.
  - The POST screen shows `...` until then, and `None` after.
  - New routines `hd_remove` and `cmos_csum` (the checksum tail of `auto_set`).
- **DEL feedback.** As soon as DEL has been seen, the status line says *Entering Setup*, during the memory test and up to the setup screen (AMI's flag, CMOS 0Eh bit 0). The memory display redraws the status line for this.
- **More room.** The 40 bytes of AMI's old memory-count print that the 4EA1h patch jumps over (4EAA-4ED1) are now a code block (`section mem`).
- `test_post.py`: 92 checks.

## Floppy change line investigation — 2026-09-24 (no ROM change)

- **Symptom on the board:** after a floppy swap, MS-DOS 7.1 shows the old directory. Windows 95 is not affected.
- **Result on the board (DSKCHG):** the change line never appears while a CF card is on the IDE bus, whether or not it is set up. It is fine with only the CF adapter, or with Seagate/WD hard disks. The CF card drives bit 7 of port 3F7h. Workaround: `DRIVPARM=/D:0 /F:7` in CONFIG.SYS (not yet tested).
- **Findings:**
  - AMI's floppy code is byte-identical in the original, ECHS and BUBios ROMs.
  - In the emulator, INT 13h 15h/16h answer identically with the original ROM and with BUBios 2.1 (change line present; 06 after a swap).
  - Most likely cause: an IDE device driving bit 7 of port 3F7h, which is shared with the floppy's change line. Details are in the README.
- **New `tools/DSKCHG.COM`** (source `tools/dskchg.asm`): shows INT 13h 15h/16h and the raw 3F7h byte live under DOS. The M/S/N keys select an IDE device before the read, to test the 3F7h theory on the board.
- **`post_emu.py`** models the change line (DIR bit 7, cleared by a step). `test_post.py` has 3 new checks (89).

## 2.1, fifth build — 2026-09-24

ROM MD5 `d240bc3299046163126bc1d5834b2e16` (replaced by the sixth build)

- **Hang at the video BIOS sign-on after power-on or RESET, found.** AMI runs this part of POST with interrupts off; on a cold start the interrupt controllers are not set up yet. The clock measurement (`measure_clock`) ended with STI and so turned interrupts on in the middle of it. Any interrupt that arrived before AMI set up the controllers then jumped through an undefined vector. It now saves and restores the interrupt flag (PUSHF/CLI … POPF). New test: the interrupt flag is still off after the POST screen's early work (the fourth build fails it).
- **Coprocessor probe moved to the end of POST**, as suggested. By then interrupts and AMI's IRQ 13 handler are in place. The field shows `...` until then, like the cache. The early F0h/F1h reset from the fourth build is gone; only the busy latch is cleared (F0h) before the probe.
- `test_post.py`: 86 checks.

## 2.1, fourth build — 2026-09-24

ROM MD5 `f70405af52233430e101d90f877a8269` (replaced by the fifth build)

Bugs found on the board with the third build:

- **Setup Summary showed "Total Memory 0K" with 64 MB.** Total = extended + 1024 KB was added in 16 bits, and 64512 + 1024 = 65536 wraps to 0. It is now added in 32 bits (65536K).
- **Memory bar jumped to 2 cells, went back to 0, then filled again (64 MB).** Before the test reaches 1 MB, the total is not known yet. BUBios used the size stored in CMOS 30h/31h by the previous boot. After fitting more RAM (16 → 64 MB), that value is too small, so the first MB looked like 1/16 of the bar. Below 1 MB the bar now stays empty (the KB count still runs). It fills from the real total as soon as AMI reports it.
- **Occasional hang after RESET** (video BIOS sign-on and AMI's ID lines on screen): this build suspected the coprocessor probe and reset the 387 first. That was not the cause; see the fifth build.
- `test_bubios.py`: 64 MB Total Memory check (74). `test_post.py`: memory bar check after a RAM upgrade (84).

## 2.1, third build — 2026-09-24

ROM MD5 `7cb6cd433fc576ce2dc9781210522f6c` (replaced by the fourth build)

The second build ran on the real board: a 20 GB master was auto-detected and used through LBA, a 4 GB slave through ECHS, and Windows 95 B installed. Visiting setup brought up these bugs, now fixed:

- **Drive names missing after setup entered during POST** (DEL before the chime). POST carries on after AMI's setup and BUBios redraws the screen, but it still thought the drives had been asked already. The drives are now asked again when setup returns. This also means switching auto-detect on in that setup finds the drives in the same POST.
- **Switching auto-detect on from the countdown setup had no effect.** The switch is stored in CMOS 7Eh/7Fh, outside AMI's checksums, so "nothing changed" and there was no restart. The switch is now part of the comparison.
- **Settings in CMOS 10h-2Dh changed from the countdown setup did not restart POST.** This covers drive types, floppies, cache and memory. The before/after comparison lost the first checksum (2Eh/2Fh) because a register was overwritten. Only changes that also moved the second checksum (34h-6Eh) restarted POST. Found while testing the fix above.
- **DEL pressed just after AMI's own DEL check was lost or booted at once.** AMI looks for DEL only during the memory test and sets a flag in CMOS 0Eh bit 0. A DEL pressed after that check, but before the countdown, was ignored. On the board it could also reach the countdown as "any other key" and boot at once. The countdown now also checks AMI's flag, and clears it, so DEL at any time from the memory test to the end of the countdown opens setup. Found while testing the fixes above in 86Box.
- `test_post.py`: six new checks for setup visits (83 in total).

## 2.1, second build — 2026-09-24

ROM MD5 `ef2babff61e23b201d0543b558b5fac2` (replaced by the third build)

After the first 2.1 build ran on the real board (countdown, auto-detect, errors and boot all fine):

- **Ports shown before the memory test.** BUBios probes COM and LPT the same way AMI does at checkpoint 9Ah and fills in the BDA early. AMI's own probe later gives the same list.
- **Clock: a sturdier early reading.** The POST timing loop now does 32 DIVs per pass instead of 8, so slow code fetches with the cache still off hardly change the result. If the early reading is still not a standard clock, it is tried again after the memory test and at the end of POST.
- **`...` placeholders** for the clock, the cache, the ports and a disk that has not answered yet, until the value is known. At the end of POST an unknown cache shows *Disabled*, no ports shows *None*.
- **Disk D: "None" with auto-detect on.** The row was left empty when there was no slave.
- **BIOS ID string** in the status bar moved one column left. It now ends in column 78, like the title bar, and is not cut off by the monitor's overscan.
- `post_emu.py` can now emulate serial and parallel ports (`com=`, `lpt=`); `test_post.py` has 77 checks.

## 2.1 — 2026-09-24 (first build)

ROM MD5 `363aeb9809439853afa0953ae00ba7cd` (replaced by the second build)

Changes after the test of 2.0 on the real board. The test brought up these points:

| Point from the 2.0 test | 2.1 |
|---|---|
| The VGA card's sign-on screen is gone | It stays on screen for 2 s before the POST screen is drawn. If the card prints nothing, POST does not wait. |
| Remove the 1 MB block counter | The memory map row is gone. The progress bar and the KB count stay. |
| DOS writes over the POST screen, in its colours | Just before the boot, the video mode is set again. DOS starts on a clear screen with normal grey-on-black text. |
| Fill in the fields early, and pause before the boot | Everything that can be known before the memory test is shown with the first screen; the cache shows `...` until AMI sets it up. A 3-second countdown runs before the boot. |
| Remove the BUBios text at the bottom right | Removed. The BIOS ID string is on the right of the status bar. |
| Why does POST crash without "(C) American Megatrends Inc.," on screen? | This is AMI's copyright check, not an error check. It is now switched off, and the line is gone. |
| HDD auto-detect and/or LBA, if there is room | Both are in. |

### New

- **Boot countdown.** After POST, the screen shows *Press DEL to run Setup, any other key to boot now ... 3* for 3 seconds.
  - DEL runs the setup. If changes were saved, POST restarts to apply them.
  - Any other key boots at once.
- **Clean start for DOS.** The video mode is set again before the boot, so DOS starts on a clear screen in normal colours.
- **Video BIOS sign-on kept.** The card's own start-up screen stays for 2 s before the POST screen (only if the card shows one).
- **Automatic hard disk detection** (Tools page: *Auto-Detect Disks at Boot*, off by default).
  - When on, IDE drives are identified at every boot and entered in CMOS as type 47 with their own geometry, before AMI sets up the disks.
  - The POST screen shows them as *Auto*.
- **LBA: INT 13h extensions** (EDD 1.1, functions 41h-44h, 47h, 48h), 28-bit LBA up to 128 GB.
  - CHS functions and the ECHS translation are unchanged.
  - The POST screen shows the LBA size of drives larger than 8.4 GB.
- **`int13x.py`** runs INT 13h calls in the POST emulator. **`asm/lba_test_mbr.asm`** is a boot sector that tests the extensions on a real or emulated machine.

### Changed

- The fields are filled in as early as possible: floppies, disk names and geometry, option ROMs, shadow RAM and battery before the memory test. The clock is shown too, if the early measurement is valid.
- The 1 MB memory map row is gone. The bar and the KB count remain.
- The BUBios line at the bottom right and the AMI copyright line are gone. AMI's copyright check (`F3DB`) is switched off. It was a tamper check, not an error check.
- The version reads 2.1 (setup title bar, Summary page, POST screen).

### Fixed (found in testing 2.1)

- After asking a missing slave for IDENTIFY, the slave stayed selected, and AMI's disk setup hung at checkpoint 91h. The master is now selected again.
- The clock value on the POST screen was printed from a clobbered register (showed 284.8 MHz in 86Box).
- The cache was briefly shown as *Disabled* before AMI enables it at the end of POST.
- The 42h/43h transfer advanced the buffer twice per sector.

### Unchanged

- The ECHS disk code: `regress_echs.py` passes.
- The order of POST: the POST checkpoint sequence is identical to 1.0.

## 2.0 — 2026-09-24

ROM MD5 `52eea91efbd108f8fff972c79e31f875` (replaced by 2.1)

### New

- **POST screen.** A full screen that replaces AMI's start-up text and its "System Configuration" box, and is filled in while POST runs:
  - processor, coprocessor, measured clock, cache, shadow RAM and RTC battery
  - a live memory test with a progress bar, a count in KB and a map with one cell per MB
  - floppies, and each hard disk with the model name read from the drive, its drive type, physical geometry, DOS (ECHS) geometry and size
  - serial and parallel ports with their addresses, and the option ROMs found
  - a status bar that shows what POST is doing and, at the end, the boot order and how long POST took, next to the AMI BIOS ID
  - AMI's error messages in red in their own rows
  - uses the setup's colour scheme, with monochrome support
- **Chime.** Two short notes instead of the single beep at the end of POST.
- **Dead code removed.** The Hard Disk Utility (unreachable since 1.0), AMI's configuration box and its cache/clock lines were cleared from the ROM: 4,501 bytes. The POST screen uses part of that space.
- **Self-contained folder.** The base ROMs and the ECHS test scripts are copied in, so this folder builds and tests on its own.
- **POST emulator** (`py/post_emu.py`) and **POST tests** (`py/test_post.py`): the whole power-on self test runs in Unicorn from the reset vector.

### Unchanged

- The setup pages. Only the version in the title bar and on the Summary page now reads 2.0.
- The ECHS disk code. `regress_echs.py` passes, as with 1.0.
- The order of POST: the POST checkpoint sequence is identical to 1.0.

### Found along the way

- AMI's POST checks that `(C) American Megatrends Inc.,` is on screen at row 21 and crashes on purpose if it is missing. The check only runs when the CMOS is invalid, so it only showed up in the test with a cleared CMOS. 2.0 kept the line at that position; 2.1 switches the check off.

## 1.0 — 2026-09-23

ROM `binary/BUBIOS_SY019L1.BIN`, MD5 `087565a6ba5e86a058275965c6bdca60`. The setup-only version, confirmed on the board. The ROM is no longer in the repository; restore it with `git show 0ccbeb2:BUBios/binary/BUBIOS_SY019L1.BIN > binary/BUBIOS_SY019L1.BIN`.

### Improvements

- **Type the date and time as numbers.** On the Standard page you can now type month, day, year, hour, minute and second with the number keys or the numpad, the same way the cylinder and head fields already worked. You no longer have to step through the values with PgUp/PgDn.
  - The field accepts the value by itself when it is full: 2 digits, or 4 for the year.
  - Enter or an arrow key accepts a shorter entry, such as `9` for September.
  - Backspace deletes a digit, and ESC cancels.
  - A two-digit year is read as 1980–2079. Invalid values, such as month 13, are ignored.
- **Hard Disk Utility removed from the menu.** AMI's low-level format, interleave and media-analysis tool can destroy data, and IDE drives and CF cards neither need it nor support it. (2.0 removed its code from the ROM too.)

### Fixes since trial 0.1

- **Math Unit** on the Summary page now finds the coprocessor (a Cyrix FasMath on the test board). Trial 0.1 read a BIOS data flag that POST only sets after setup has run, so it always showed "None". BUBios now tests the coprocessor directly.
- **Arrow keys no longer switch tabs.** Only Tab and Shift-Tab move between the main tabs. The arrow keys only work inside the current page.
- **Title bar:** the leading space before "BUBios (tm)" is gone.
- **Version** is now 1.0, in the title bar and on the Summary page.

`test_bubios.py`: 67 checks.

## 0.1 trial — 2026-09-23

First version: an MR-BIOS-style front end on top of the ECHS ROM.

- Tabs: Summary, Standard, Advanced, Chipset, Tools, Exit
- Summary page
- Tab and Shift-Tab work inside the AMI pages
- Six colour schemes

The AMI "Improper use of setup" warning screen and the Hard Disk Utility were already left out of the menu in this version.
