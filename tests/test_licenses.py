import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from license_inventory import permitted  # noqa: E402


def test_spdx_evaluation():
    for ok in ("MIT", "Apache-2.0", "MIT OR Apache-2.0", "MIT/Apache-2.0", "(MIT OR Apache-2.0) AND Unicode-3.0",
               "Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT", "MIT OR GPL-3.0", "Apache 2.0 License"):
        assert permitted(ok), ok
    for bad in ("GPL-3.0", "AGPL-3.0-only", "MIT AND GPL-3.0", "Proprietary", "", "LicenseRef-Commercial", "SSPL-1.0"):
        assert not permitted(bad), bad


def test_python_dependencies_are_permissive():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "license_inventory.py"), "--python"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
