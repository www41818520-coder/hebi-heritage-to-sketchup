#!/usr/bin/env python3
"""Load ASCII DXF while detecting a false legacy-codepage declaration."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any


CODEPAGE_ENCODINGS = {
    "ANSI_936": "gb18030",
    "ANSI_950": "big5",
    "ANSI_932": "shift_jis",
    "ANSI_949": "euc_kr",
    "UTF-8": "utf-8-sig",
}


def declared_codepage(raw: bytes) -> str:
    probe = raw[:100_000].decode("ascii", errors="ignore")
    match = re.search(r"\$DWGCODEPAGE\s*\r?\n\s*3\s*\r?\n([^\r\n]+)", probe)
    return match.group(1).strip().upper() if match else ""


def decode_ascii_dxf(path: Path) -> tuple[str, dict[str, Any]]:
    raw = path.read_bytes()
    if raw.startswith(b"AutoCAD Binary DXF"):
        raise ValueError("Binary DXF is not supported; export ASCII DXF instead.")
    codepage = declared_codepage(raw)
    declared_encoding = CODEPAGE_ENCODINGS.get(codepage, "utf-8-sig")
    contains_non_ascii = any(byte >= 128 for byte in raw)
    override = False
    text: str | None = None
    if contains_non_ascii and declared_encoding != "utf-8-sig":
        try:
            text = raw.decode("utf-8-sig", errors="strict")
            override = True
        except UnicodeDecodeError:
            pass
    if text is None:
        text = raw.decode(declared_encoding, errors="replace")
    return text, {
        "declared_codepage": codepage or "unspecified",
        "declared_encoding": declared_encoding,
        "effective_encoding": "utf-8-sig" if override else declared_encoding,
        "encoding_override": override,
        "override_reason": "non_ascii_payload_is_valid_utf8" if override else "",
    }


def read_dxf(path: Path) -> tuple[Any, dict[str, Any]]:
    import ezdxf  # type: ignore

    _, encoding = decode_ascii_dxf(path)
    if encoding["encoding_override"]:
        # Preserve every source byte, including binary-data group values. Only
        # repair the false header declaration in a temporary DXF so ezdxf
        # decodes structured text as UTF-8 without round-tripping binary tags.
        raw = path.read_bytes()
        repaired, count = re.subn(
            rb"(\$DWGCODEPAGE\s*\r?\n\s*3\s*\r?\n)[^\r\n]+",
            rb"\1UTF-8",
            raw,
            count=1,
        )
        if count != 1:
            raise ValueError("DXF encoding override was required but $DWGCODEPAGE could not be repaired")
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as stream:
            stream.write(repaired)
            repaired_path = Path(stream.name)
        try:
            doc = ezdxf.readfile(repaired_path)
        finally:
            repaired_path.unlink(missing_ok=True)
        encoding["loader"] = "ezdxf.readfile(repaired_codepage_copy)"
    else:
        doc = ezdxf.readfile(path)
        encoding["loader"] = "ezdxf.readfile"
    encoding["loader_encoding"] = str(getattr(doc, "encoding", ""))
    return doc, encoding
