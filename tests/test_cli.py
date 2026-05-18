import subprocess
import sys


def test_cli_help_exits_zero():
    r = subprocess.run(
        [sys.executable, "-m", "scan_and_identify.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "serve" in r.stdout
    assert "build-catalog" in r.stdout
