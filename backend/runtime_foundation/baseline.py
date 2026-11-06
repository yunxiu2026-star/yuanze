from __future__ import annotations

import hashlib
import json
from pathlib import Path


def verify_manifest(source_root: Path, manifest_path: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mismatches: list[str] = []
    canonical: list[tuple[str, str]] = []
    total = 0
    for item in manifest["files"]:
        path = source_root / Path(item["path"])
        if not path.is_file():
            mismatches.append(item["path"] + ":missing")
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        total += len(data)
        if digest != item["sha256"] or len(data) != item["bytes"]:
            mismatches.append(item["path"] + ":identity")
        normalized_path = item["path"].replace(chr(92), "/")
        canonical.append((normalized_path, f"{digest}  {normalized_path}\n"))
    root_hash = hashlib.sha256("".join(line for _, line in sorted(canonical, key=lambda pair: pair[0])).encode("utf-8")).hexdigest()
    return {"mismatches": mismatches, "file_count": len(canonical), "byte_count": total, "root_sha256": root_hash, "root_match": root_hash == manifest["root_sha256"]}
