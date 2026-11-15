"""Pack BBUtils into an installable Blender add-on / extension zip.

Usage:  python build.py
Output: dist/BBUtils-<version>.zip

Layout inside the zip:
    bbutils/__init__.py           (copy of BBUtils.py)
    bbutils/blender_manifest.toml

Blender 4.2+: Edit > Preferences > Get Extensions > (v) > Install from Disk.
"""
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "BBUtils.py"
MANIFEST = ROOT / "blender_manifest.toml"
PKG = "bbutils"


def main():
    src = SOURCE.read_text(encoding="utf-8")
    manifest = MANIFEST.read_text(encoding="utf-8")

    m = re.search(r'"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)', src)
    if not m:
        raise SystemExit("bl_info version not found in BBUtils.py")
    version = ".".join(m.groups())

    # keep the manifest version in sync with bl_info
    manifest = re.sub(r'^version = ".*"$', f'version = "{version}"',
                      manifest, count=1, flags=re.M)
    MANIFEST.write_text(manifest, encoding="utf-8")

    out_dir = ROOT / "dist"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"BBUtils-{version}.zip"

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{PKG}/__init__.py", src)
        z.writestr(f"{PKG}/blender_manifest.toml", manifest)

    print(f"Built {out}")


if __name__ == "__main__":
    main()
