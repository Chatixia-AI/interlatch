"""Print what user.ps1 left in C:\\qa: its log, then every step's stdout and stderr, saying when bytes are not UTF-8."""

import sys
from pathlib import Path

QA = Path("C:/qa")


def show(path: Path) -> None:
    data = path.read_bytes()
    if not data.strip():
        return
    try:
        text, note = data.decode("utf-8-sig"), ""
    except UnicodeDecodeError:
        odd = bytes(b for b in data if b > 0x7F)[:32].hex(" ")
        text, note = data.decode("cp932", "replace"), f"  [NOT UTF-8: bytes {odd} ...; shown as cp932]"
    print(f"----- {path.name}{note}\n{text.rstrip()[-6000:]}")


sys.stdout.reconfigure(encoding="utf-8")
log = QA / "user.log"
print(log.read_text(encoding="utf-8-sig") if log.exists() else "(no user.log)")
for p in sorted(QA.glob("*.out")) + sorted(QA.glob("*.err")):
    show(p)
logs = Path("C:/Users").glob("*/.interlatch/logs/*.log")
for p in logs:
    show(p)
