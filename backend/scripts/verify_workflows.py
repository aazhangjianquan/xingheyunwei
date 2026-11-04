"""Compatibility entry point: fixed chains have been replaced by V3 workflows."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).with_name('verify_v3.py')), run_name='__main__')
