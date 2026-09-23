# Changelog

This repository has two parts. This file covers the first one, the ECHS
large-disk patch. The setup UI built on top of it has its own changelog:
[`BUBios/CHANGELOG.md`](BUBios/CHANGELOG.md).

## ECHS large-disk patch

### v5 — release

`binary/SY019L1_27C512_CHSPATCH.BIN`, MD5 `8e520e91100f932d3f054883b08d37da`

Confirmed on the board:

- 8 GB CF card, 16000/16/63 → 1002/255/63: FDISK, `FORMAT /S`, file copy
  and a full-surface benchmark all work, and the board boots from C:.
- WD Caviar 24300 and a 2 GB SanDisk CF card work unchanged.

### Development history (v1 → v5)

Six bugs were found and fixed on the way to v5. `BIOS_Evaluation.md`
§3–§8 describes each one with its symptom and diagnosis.

1. `xlate_getparams` lost DX across `calc_factor`, so AH=08h always
   reported 1023 cylinders.
2. The patch region overwrote the AH=08h handler's own `RET`. Every
   Get Parameters call fell through into AH=09h, and booting from floppy
   froze whenever a hard disk was configured.
3. The patch region also overwrote the handler tail that loads
   DL = drive count. DOS and FDISK then saw "no fixed disk" on every drive
   whose (RealCyl mod factor) was zero.
4. The translation wrote the physical cylinder high byte into `[bp+15h]`,
   the caller's saved DH, which corrupted DX on every call.
5. `PUSHA`/`POPA` around 32-bit math zeroed the upper halves of EAX, ECX
   and EDX. It now uses `PUSHAD`/`POPAD`.
6. The head count could reach 256, which caused FDISK to report "Error
   writing fixed disk" on 8 GB cards and some geometries to hang. The
   head ladder now ends at 255.

`CHS_TRANSLATION_PORTING_GUIDE.md` turns these lessons into a method that
can be reused on other BIOSes.
