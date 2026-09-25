# Notes for AI assistants

This repository documents a finished BIOS modding project: an ECHS
large-disk patch (repository root) and BUBios (`BUBios/`), both for the
AMI BIOS 11/11/92 of the SOYO SY-019L1 386 board. It is also meant as a
reference for modding other legacy BIOSes.

- **Start with [`Project_Overview.md`](Project_Overview.md).** Its section
  2 says which document answers which question.
- **The ROM images are final and confirmed on hardware.** Do not modify,
  rebuild over, or rename any file in `binary/`, `BUBios/binary/` or
  `BUBios/tools/*.COM` unless the user explicitly starts a new version.
  Rebuilding writes the ROM in place, so run a build only when asked to,
  and compare the MD5 with the one in `Project_Overview.md`.
- **Modding another BIOS:** read
  [`docs/CHS_TRANSLATION_PORTING_GUIDE.md`](docs/CHS_TRANSLATION_PORTING_GUIDE.md)
  first (mapping an unknown ROM, ECHS, emulation), then
  [`docs/BIOS_MODDING_GUIDE.md`](docs/BIOS_MODDING_GUIDE.md) (space,
  POST hooks, setup, LBA). Use `BUBios/asm/`, `BUBios/py/` and
  [`docs/BIOS_ADDRESS_MAP.md`](docs/BIOS_ADDRESS_MAP.md) as worked
  examples, and remember that every address is specific to this ROM.
- **Build on the emulators.** `py/ide_harness.py`,
  `BUBios/py/setup_emu.py` and `BUBios/py/post_emu.py` run the real ROM
  code in Unicorn. Many bugs in this project were found with them, often
  by comparing a patched ROM with the original under identical inputs.
- `BUBios/py/test_post.py` takes 25-35 minutes.
