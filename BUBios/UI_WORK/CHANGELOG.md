# BUBios changelog

## 1.0 — 2026-09-23

ROM `binary/BUBIOS_SY019L1.BIN`, MD5 `087565a6ba5e86a058275965c6bdca60`

### Improvements

- **Type the date and time as numbers.** On the Standard page you can now type month, day, year, hour, minute and second with the number keys or the numpad, the same way the cylinder and head fields already worked. You no longer have to step through the values with PgUp/PgDn.
  - The field accepts the value by itself when it is full: 2 digits, or 4 for the year.
  - Enter or an arrow key accepts a shorter entry, such as `9` for September.
  - Backspace deletes a digit, and ESC cancels.
  - A two-digit year is read as 1980–2079. Invalid values, such as month 13, are ignored.
- **Hard Disk Utility removed.** AMI's low-level format, interleave and media-analysis tool is no longer in the menu. It can destroy data, and IDE drives and CF cards neither need it nor support it. The code is still in the ROM; it just can't be reached.

### Fixes since trial 0.1

- **Math Unit** on the Summary page now finds the coprocessor (a Cyrix FasMath on the test board). Trial 0.1 read a BIOS data flag that POST only sets after setup has run, so it always showed "None". BUBios now tests the coprocessor directly.
- **Arrow keys no longer switch tabs.** Only Tab and Shift-Tab move between the main tabs. The arrow keys only work inside the current page.
- **Title bar:** the leading space before "BUBios (tm)" is gone.
- **Version** is now 1.0, in the title bar and on the Summary page.

## 0.1 trial — 2026-09-23

First version: an MR-BIOS-style front end on top of the ECHS ROM.

- Tabs: Summary, Standard, Advanced, Chipset, Tools, Exit
- Summary page
- Tab and Shift-Tab work inside the AMI pages
- Six colour schemes

The AMI "Improper use of setup" warning screen and the Hard Disk Utility were already left out of the menu in this version.
