import io
import zipfile
from pathlib import Path


def build_zip(file_paths: list[Path]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in file_paths:
            if p.exists():
                zf.write(p, arcname=p.name)
    buf.seek(0)
    return buf
