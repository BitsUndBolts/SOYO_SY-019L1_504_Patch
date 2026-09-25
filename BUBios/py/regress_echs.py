#!/usr/bin/env python3
"""Run the ECHS INT 13h regression (regress.py) and the INT 19h boot test
(boot_test.py) of the ECHS project (repository root) against the BUBios ROM, to show
that the disk code of the hardware-confirmed ECHS ROM still behaves
identically.  Copies of both scripts and of ide_harness.py are in this
folder, so it runs on its own.

    python3 py/regress_echs.py
"""
import os, runpy, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ide_harness
print('ROM under test:', ide_harness.PATCHED_ROM)
for script in ('regress.py', 'boot_test.py'):
    print('\n=== %s ===' % script)
    try:
        runpy.run_path(os.path.join(HERE, script), run_name='__main__')
    except SystemExit as e:
        if e.code not in (0, None): print('exit code', e.code); sys.exit(e.code)
