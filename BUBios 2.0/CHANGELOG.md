# BUBios changelog

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

- AMI's POST checks that `(C) American Megatrends Inc.,` is on screen at row 21 and crashes on purpose if it is missing. The check only runs when the CMOS is invalid, so it only showed up in the test with a cleared CMOS. The POST screen keeps the line at that position (details in the README).

## 1.0 — 2026-09-23

ROM `../BUBios/binary/BUBIOS_SY019L1.BIN`, MD5 `087565a6ba5e86a058275965c6bdca60`

See `../BUBios/CHANGELOG.md`: numeric date and time entry, the Hard Disk Utility removed from the menu, Math Unit, tab and title-bar fixes.

## 0.1 trial — 2026-09-23

First version: an MR-BIOS-style front end on top of the ECHS ROM.
