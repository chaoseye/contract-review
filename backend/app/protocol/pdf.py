"""PDF протокола: тот же docx, переведённый LibreOffice. Поэтому Word и PDF выглядят одинаково."""

import tempfile
from pathlib import Path

from app.documents.convert import convert


def docx_to_pdf(docx: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="contract-review-protocol-") as work:
        source = Path(work) / "protocol.docx"
        source.write_bytes(docx)
        return convert(source, "pdf", Path(work) / "out").read_bytes()
