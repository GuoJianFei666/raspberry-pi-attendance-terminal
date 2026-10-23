"""Read-only inspection for wider_face; only this project dataset is included."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import statistics
import tarfile
import time
import urllib.request
import zipfile


def dump(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def obtain(spec, cache, download):
    target = cache / spec["filename"]
    if not target.exists():
        if not download:
            raise FileNotFoundError(f"Missing {target}; use --download to retrieve public data")
        print(f"Downloading {spec['id']}", flush=True)
        request = urllib.request.Request(spec["url"], headers={"User-Agent": "project-public-data/1.0"})
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=20) as response:
            chunks = []
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                if time.monotonic() - started > 180:
                    raise TimeoutError(f"Total download time exceeds 180 seconds: {spec['id']}")
            body = b"".join(chunks)
        expected = spec.get("sha256")
        actual = hashlib.sha256(body).hexdigest()
        if expected and actual != expected:
            raise ValueError(f"SHA256 mismatch for {spec['id']}: {actual}")
        target.write_bytes(body)
    body = target.read_bytes()
    actual = hashlib.sha256(body).hexdigest()
    if spec.get("sha256") and spec["sha256"] != actual:
        raise ValueError(f"Cached SHA256 mismatch: {spec['id']}")
    git_sha = hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()
    if spec.get("github_blob_sha") and git_sha != spec["github_blob_sha"]:
        raise ValueError(f"GitHub blob SHA mismatch: {spec['id']}")
    return {**spec, "bytes": len(body), "sha256": actual,
            "github_blob_verified": bool(spec.get("github_blob_sha"))}


def inspect_wider(cache):
    result = {}
    with zipfile.ZipFile(cache / "wider_face_split.zip") as archive:
        for split in ("train", "val"):
            candidates = [n for n in archive.namelist() if n.endswith(f"wider_face_{split}_bbx_gt.txt")]
            if len(candidates) != 1:
                raise ValueError(f"Missing/ambiguous WIDER annotation {split}")
            lines = archive.read(candidates[0]).decode("utf-8-sig").splitlines()
            index = images = face_rows = positive_boxes = zero_count_placeholders = 0
            invalid_flag = Counter()
            while index < len(lines):
                image_name = lines[index].strip()
                if not image_name.endswith(".jpg"):
                    raise ValueError(f"Invalid WIDER image boundary at line {index+1}")
                count = int(lines[index+1])
                index += 2
                images += 1
                if count == 0:
                    if index < len(lines) and not lines[index].strip().endswith(".jpg"):
                        # The official file includes a zero-box placeholder for an empty image.
                        placeholder = [int(x) for x in lines[index].split()]
                        if len(placeholder) != 10 or any(placeholder):
                            raise ValueError("Unexpected WIDER zero-face placeholder")
                        zero_count_placeholders += 1
                        index += 1
                    continue
                for _ in range(count):
                    box = [int(x) for x in lines[index].split()]
                    if len(box) != 10:
                        raise ValueError("Expected 10 WIDER box fields")
                    positive_boxes += box[2] > 0 and box[3] > 0
                    invalid_flag[str(box[7])] += 1
                    face_rows += 1
                    index += 1
            result[split] = {"images": images, "annotated_face_rows": face_rows,
                "positive_size_boxes": positive_boxes, "invalid_flag_counts": dict(invalid_flag),
                "zero_count_placeholder_lines": zero_count_placeholders}
    result["scope"] = "Text bounding-box annotations only; no image or identity validation, no detector results"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'data/downloads.json').read_text(encoding='utf-8'))
    args.cache.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda spec: obtain(spec, args.cache, args.download), manifest['files']))
    result = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'method': 'Python standard-library read-only data inspection; not model or application evaluation',
              'wider_face': inspect_wider(args.cache)}
    dump(args.output, result)
    dump(args.output.with_name('source-files.json'), {'files': records})
    print(json.dumps({'dataset': 'wider_face', 'inspected_files': len(records)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
