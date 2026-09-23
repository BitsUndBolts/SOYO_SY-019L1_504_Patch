#!/usr/bin/env python3
"""Re-run the parent project's ECHS INT 13h regression (py/regress.py) and
INT 19h boot test (py/boot_test.py) against the BUBios ROM, to show that the
disk code of the hardware-confirmed ECHS ROM still behaves identically.

    python3 py/regress_echs.py
"""
import os, runpy, sys
HERE = os.path.dirname(os.path.abspath(__file__))
PARENT_PY = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'py')
sys.path.insert(0, PARENT_PY)
import ide_harness
ide_harness.PATCHED_ROM = os.path.join(os.path.dirname(HERE), 'binary', 'BUBIOS_SY019L1.BIN')
print('ROM under test:', ide_harness.PATCHED_ROM)
for script in ('regress.py', 'boot_test.py'):
    print('\n=== parent py/%s ===' % script)
    try:
        runpy.run_path(os.path.join(PARENT_PY, script), run_name='__main__')
    except SystemExit as e:
        if e.code not in (0, None): print('exit code', e.code); sys.exit(e.code)
