#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Create a Debian package from reviewed source and hash-locked runtime wheels."""

import hashlib
import gzip
import os
from email.utils import parsedate_to_datetime
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

root = Path(__file__).resolve().parents[1]
project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
version = project["version"].replace(".dev", "~dev")
architecture = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
out = root / "dist"
out.mkdir(exist_ok=True)
wheelhouse = root / ".build/wheels"
wheelhouse.mkdir(parents=True, exist_ok=True)
requirements = root / "requirements.lock"
changelog = (root / "packaging/changelog").read_text()
release_date = next(
    line.split(">  ", 1)[1] for line in changelog.splitlines() if line.startswith(" -- ")
)
epoch = int(
    os.environ.get("SOURCE_DATE_EPOCH", parsedate_to_datetime(release_date).timestamp())
)
package_env = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch)}
assert (
    f'__version__ = "{project["version"]}"' in (root / "src/dictadesk/__init__.py").read_text()
)
if architecture != "amd64":
    raise SystemExit("The checked dependency lock and release package target amd64.")
if not requirements.is_file():
    raise SystemExit(
        "Missing requirements.lock; release dependencies must be reviewed and pinned."
    )
subprocess.run(
    [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--require-hashes",
        "--only-binary=:all:",
        "-r",
        str(requirements),
        "--dest",
        str(wheelhouse),
    ],
    check=True,
)
with tempfile.TemporaryDirectory(prefix="dictadesk-deb-") as temporary:
    stage = Path(temporary)
    lib = stage / "usr/lib/dictadesk"
    lib.mkdir(parents=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--ignore-installed",
            "--no-compile",
            "--only-binary=:all:",
            "--require-hashes",
            "--find-links",
            str(wheelhouse),
            "--target",
            str(lib / "vendor"),
            "-r",
            str(requirements),
        ],
        check=True,
    )
    # Discard unused pip-generated entry points containing build-machine paths.
    shutil.rmtree(lib / "vendor/bin", ignore_errors=True)
    shutil.copytree(
        root / "src/dictadesk",
        lib / "dictadesk",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    binary = stage / "usr/bin/dictadesk"
    binary.parent.mkdir(parents=True)
    binary.write_text(
        '#!/usr/bin/python3\nimport sys\nsys.path[:0] = ["/usr/lib/dictadesk", "/usr/lib/dictadesk/vendor"]\nfrom dictadesk.ui.application import main\nmain()\n'
    )
    binary.chmod(0o755)
    desktop = stage / "usr/share/applications"
    desktop.mkdir(parents=True)
    shutil.copy2(root / "data/io.github.woodpile72.DictaDesk.desktop", desktop)
    icons = stage / "usr/share/icons/hicolor/scalable/apps"
    icons.mkdir(parents=True)
    for file in (root / "src/dictadesk/resources").glob("*.svg"):
        shutil.copy2(file, icons)
    docs = stage / "usr/share/doc/dictadesk"
    docs.mkdir(parents=True)
    for filename in (
        "LICENSE",
        "NOTICE",
        "README.rst",
        "DEVELOPMENT.rst",
        "PROVENANCE.txt",
        "requirements.lock",
    ):
        shutil.copy2(root / filename, docs)
    shutil.copy2(root / "dictadesk-iconset/README.txt", docs / "artwork.txt")
    shutil.copytree(root / "media/screenshots", docs / "media/screenshots")
    shutil.copytree(root / "packaging/licenses", docs / "licenses")
    shutil.copy2(root / "packaging/copyright", docs / "copyright")
    (docs / "changelog.gz").write_bytes(gzip.compress(changelog.encode(), mtime=0))
    man = stage / "usr/share/man/man1"
    man.mkdir(parents=True)
    (man / "dictadesk.1.gz").write_bytes(
        gzip.compress((root / "packaging/dictadesk.1").read_bytes(), mtime=0)
    )
    cache = stage / "var/cache/dictadesk"
    cache.mkdir(parents=True)
    cache.chmod(0o1777)
    control = stage / "DEBIAN"
    control.mkdir()
    shutil.copy2(root / "packaging/postrm", control / "postrm")
    (control / "postrm").chmod(0o755)
    size = sum(file.stat().st_size for file in stage.rglob("*") if file.is_file()) // 1024
    (control / "control").write_text(f"""Package: dictadesk
Version: {version}
Section: utils
Priority: optional
Architecture: {architecture}
Maintainer: Erick Woods <woodpile72@users.noreply.github.com>
Depends: python3 (>= 3.12), python3 (<< 3.13), python3-gi, python3-cairo, python3-gi-cairo, gir1.2-gtk-4.0 (>= 4.14), gir1.2-adw-1 (>= 1.5), gir1.2-atspi-2.0, at-spi2-core, pulseaudio-utils, libportaudio2, libsndfile1, libstdc++6, libgcc-s1, libc6
Installed-Size: {size}
Homepage: https://github.com/woodpile72/DictaDesk
Description: Desktop Dictation for Linux
 Independent GTK 4 voice dictation with local speech recognition,
 tap-to-start dictation, a status tray, vocabulary and clipboard fallback.
""")
    (control / "control").chmod(0o644)
    # Stable timestamps and permissions make a frozen source build repeatable.
    for item in sorted(stage.rglob("*"), reverse=True):
        if item.is_symlink():
            continue
        if item.is_dir():
            item.chmod(0o1777 if item == cache else 0o755)
        else:
            item.chmod(0o755 if item == binary or item == control / "postrm" else 0o644)
        os.utime(item, (epoch, epoch))
    stage.chmod(0o755)
    os.utime(stage, (epoch, epoch))
    deb = out / f"dictadesk_{version}_{architecture}.deb"
    subprocess.run(
        ["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(stage), str(deb)],
        check=True,
        env=package_env,
    )
    (out / (deb.name + ".sha256")).write_text(
        hashlib.sha256(deb.read_bytes()).hexdigest() + "  " + deb.name + "\n"
    )
    (out / "SHA256SUMS").write_text((out / (deb.name + ".sha256")).read_text())
    print(deb)
