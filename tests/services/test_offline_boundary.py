import subprocess
import sys
import time


def test_fresh_python_process_rejects_network_before_dns():
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-c",
        "import socket; socket.create_connection(('data.alpaca.markets', 443))"],
        capture_output=True, text=True, timeout=3)
    assert result.returncode != 0
    assert "Unexpected network access in offline test" in result.stderr
    assert time.monotonic() - started < 3
