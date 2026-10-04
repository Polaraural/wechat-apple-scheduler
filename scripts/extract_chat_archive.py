#!/usr/bin/env python3
"""Extract a chat export into an agent-readable folder and manifest.

Accepts either a ZIP archive or an already-extracted directory. The directory
form matters in hosted sandboxes where the original upload is unpacked and the
ZIP never touches disk.
"""
from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def safe_relative(name: str) -> Path:
    parts = []
    for part in Path(name).parts:
        if part in ("", ".", "..", "/"):
            continue
        parts.append(re.sub(r'[\x00-\x1f]', "_", part))
    return Path(*parts) if parts else Path("unnamed")


def repair_zip_name(name: str) -> str:
    """Undo cp437 mojibake for archives whose entry names lack the UTF-8 flag.

    Info-ZIP and several Windows/WeChat exporters store UTF-8 or GBK bytes
    without setting flag bit 11, so Python decodes them as cp437. Names that
    were decoded correctly (real CJK) fail the cp437 encode and pass through
    untouched, which keeps the repair safe.
    """
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    if raw.isascii():
        return name
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return name


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(2, 10_000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot allocate unique path for {path}")


def iter_source_entries(source: Path):
    """Yield (name, bytes) pairs from a ZIP archive or a plain directory."""
    if source.is_dir():
        for item in sorted(source.rglob("*")):
            if item.is_dir() or item.name == ".DS_Store":
                continue
            yield str(item.relative_to(source)), item.read_bytes()
        return
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            if info.is_dir() or Path(info.filename).name == ".DS_Store":
                continue
            yield repair_zip_name(info.orig_filename), archive.read(info)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path, help="ZIP archive or already-extracted directory")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not args.archive.exists():
        parser.error(f"source not found: {args.archive}")

    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "archive": str(args.archive.resolve()),
        "source_type": "directory" if args.archive.is_dir() else "zip",
        "files": [],
        "texts": [],
        "images": [],
    }

    for name, data in iter_source_entries(args.archive):
        target = unique_path(args.output / safe_relative(name))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        entry = {
            "archive_name": name,
            "path": str(target.resolve()),
            "size_bytes": len(data),
        }
        manifest["files"].append(entry)
        suffix = target.suffix.lower()
        if suffix in {".txt", ".md", ".csv", ".json"}:
            text = decode_text(data)
            text_path = target if suffix in {".txt", ".md"} else target.with_suffix(target.suffix + ".txt")
            if text_path != target:
                text_path.write_text(text, encoding="utf-8")
            entry["text_path"] = str(text_path.resolve())
            manifest["texts"].append({"path": str(text_path.resolve()), "preview": text[:4000]})
        if suffix in {".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp", ".bmp", ".tif", ".tiff"}:
            manifest["images"].append(str(target.resolve()))

    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
