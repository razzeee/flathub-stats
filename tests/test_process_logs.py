import json
import lzma
import os
import subprocess
import sys
import time
from pathlib import Path


def test_rotation_preserves_pending_logs_and_archives(tmp_path):
    logs = tmp_path / "logs with spaces"
    logs.mkdir()
    source = logs / "access.log"
    pending = logs / "access.log.rotated"
    archive = logs / "access.log.rotated.xz"
    source.write_text("new log")
    pending.write_text("pending log")
    archive.write_bytes(lzma.compress(b"previously archived log"))
    old = time.time() - 6 * 3600
    os.utime(source, (old, old))

    # Leave a colliding archive in place to exercise ingestion failure first.
    # The archive must not be overwritten by either rotation or compression.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    interpreter = bin_dir / "python"
    record = tmp_path / "arguments.json"
    interpreter.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['ARGUMENTS'], 'w') as stream:\n"
        "    json.dump(sys.argv[1:], stream)\n"
        "sys.exit(int(os.environ.get('FAIL_PROCESSING', '0')))\n"
    )
    interpreter.chmod(0o755)
    systemctl = bin_dir / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 0\n")
    systemctl.chmod(0o755)
    env = {
        **os.environ,
        "PYTHON": str(interpreter),
        "ARGUMENTS": str(record),
        "FAIL_PROCESSING": "1",
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }
    command = [
        "bash",
        str(Path(__file__).resolve().parents[1] / "process-logs.sh"),
        str(logs),
        str(tmp_path / "stats with spaces"),
    ]
    result = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert not source.exists()
    assert sorted(path.read_text() for path in logs.glob("*.log.rotated")) == [
        "new log",
        "pending log",
    ]
    assert lzma.decompress(archive.read_bytes()) == b"previously archived log"
    arguments = json.loads(record.read_text())
    assert len(arguments) == 5  # script, --dest, destination, two input paths
    assert {Path(arg) for arg in arguments[3:]} == set(logs.glob("*.log.rotated"))

    # The retry must preserve the colliding archive without manual intervention.
    env["FAIL_PROCESSING"] = "0"
    subprocess.run(command, env=env, cwd=tmp_path, check=True, capture_output=True)
    assert not list(logs.glob("*.log.rotated"))
    assert sorted(lzma.decompress(path.read_bytes()) for path in logs.glob("*.xz")) == [
        b"new log",
        b"pending log",
        b"previously archived log",
    ]
