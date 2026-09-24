# BUBios changelog

## 2.1, fifth build — 2026-09-24

ROM `binary/BUBIOS2_SY019L1.BIN`, MD5 `d240bc3299046163126bc1d5834b2e16`

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

Changes after the test of 2.0 on the real board.

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

ROM `binary/BUBIOS2_SY019L1.BIN`, MD5 `52eea91efbd108f8fff972c79e31f875`

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

ROM `../BUBios/binary/BUBIOS_SY019L1.BIN`, MD5 `087565a6ba5e86a058275965c6bdca60`

See `../BUBios/CHANGELOG.md`: numeric date and time entry, the Hard Disk Utility removed from the menu, Math Unit, tab and title-bar fixes.

## 0.1 trial — 2026-09-23

First version: an MR-BIOS-style front end on top of the ECHS ROM.
