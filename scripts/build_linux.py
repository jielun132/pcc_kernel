"""Build on the declared Linux baseline; never cross-claim host compatibility."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd=ROOT):
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--arch", choices=["x86_64", "aarch64"], required=True)
    p.add_argument("--distro", required=True, help="Exact /etc/os-release ID:VERSION_ID, e.g. ubuntu:22.04")
    p.add_argument("--glibc", required=True, help="Exact build-host glibc baseline, e.g. 2.35")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--wheelhouse", type=Path, help="Use an existing offline runtime wheelhouse")
    args = p.parse_args()
    if platform.system() != "Linux" or sys.version_info[:2] != (3, 11):
        p.error("Build requires native Linux and CPython 3.11; cross-compiling is not a release validation")
    os_release = platform.freedesktop_os_release()
    distro = os_release.get("ID", "") + ":" + os_release.get("VERSION_ID", "")
    libc, libc_version = platform.libc_ver()
    if platform.machine() != args.arch or distro != args.distro or libc != "glibc" or libc_version != args.glibc:
        p.error("Build host does not match the declared architecture, distribution, or glibc baseline")
    output = args.out.absolute()
    if output.exists():
        p.error("Output exists; use a new output directory")
    version = importlib.metadata.version("pcc-tool")
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    if version != declared:
        p.error("Installed package metadata is stale; reinstall the project before building")
    output.mkdir(parents=True)
    target = output / f"pcc-core-{version}-linux-{args.arch}"
    run(sys.executable, "-m", "pytest", "-q")
    run(sys.executable, "-m", "build", "--no-isolation", "--outdir", output / "wheels")
    run(sys.executable, "-m", "PyInstaller", "--onedir", "--name", "pcc", "--paths", ROOT / "src",
        "--distpath", output / "frozen", "--workpath", output / "work", "--specpath", output,
        "--collect-all", "sqlglot", "--collect-all", "pymysql", "--collect-data", "pcc_tool",
        "--copy-metadata", "pcc-tool", "--copy-metadata", "PyMySQL", "--copy-metadata", "sqlglot",
        ROOT / "packaging/pcc_entry.py")
    shutil.copytree(output / "frozen/pcc", target)
    shutil.copytree(ROOT / "docs", target / "docs",
                    ignore=shutil.ignore_patterns("test-report-*.md", "sql-coverage-report-*.md"))
    shutil.copytree(ROOT / "examples", target / "examples")
    shutil.copy2(ROOT / "README.md", target / "README.md")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", target / "THIRD_PARTY_NOTICES.md")
    for name in ("LICENSE", "NOTICE", "CONTRIBUTING.md", "SECURITY.md"):
        shutil.copy2(ROOT / name, target / name)
    shutil.copy2(ROOT / "requirements.lock", target / "requirements.lock")
    shutil.copytree(output / "wheels", target / "sdk")
    wheelhouse = target / "wheelhouse"
    if args.wheelhouse:
        shutil.copytree(args.wheelhouse, wheelhouse)
    else:
        run(sys.executable, "-m", "pip", "download", "--only-binary=:all:", "-r", ROOT / "requirements.lock", "--dest", wheelhouse)
    # Validate that the copied wheelhouse can satisfy exact runtime requirements offline.
    run(sys.executable, "-m", "pip", "download", "--no-index", "--find-links", wheelhouse, "--only-binary=:all:",
        "-r", ROOT / "requirements.lock", "--dest", output / "wheelhouse-verification")
    licenses = target / "licenses"
    licenses.mkdir()
    for name in ("PyMySQL", "sqlglot", "pyinstaller", "pyinstaller-hooks-contrib"):
        dist = importlib.metadata.distribution(name)
        files = [f for f in dist.files or [] if "license" in Path(f).name.lower() or "copying" in Path(f).name.lower()]
        if not files:
            raise RuntimeError(f"Missing dependency license: {name}")
        for index, file in enumerate(files):
            shutil.copy2(dist.locate_file(file), licenses / f"{name}-{index}-{Path(file).name}")
    python_license = next((p for p in [Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE",
                                      Path(sys.base_prefix) / "lib/python3.11/LICENSE.txt"] if p.exists()), None)
    if python_license is None:
        raise RuntimeError("CPython license is missing; include the runtime distribution license before release")
    shutil.copy2(python_license, licenses / "CPython-LICENSE.txt")
    info = {"version": version, "python": platform.python_version(), "arch": args.arch, "distribution": distro,
            "glibc": libc_version, "status": "built_not_release_certified",
            "dependencies": {n: importlib.metadata.version(n) for n in ("PyMySQL", "sqlglot", "pyinstaller")}}
    (target / "build-info.json").write_text(json.dumps(info, indent=2) + "\n")
    smoke = subprocess.run([str(target / "pcc"), "doctor"], env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                           cwd=target, check=True, capture_output=True, text=True)
    (output / "frozen-smoke.json").write_text(smoke.stdout)
    checksums = []
    for file in sorted(target.rglob("*")):
        if file.is_file():
            checksums.append(hashlib.sha256(file.read_bytes()).hexdigest() + "  " + file.relative_to(target).as_posix())
    (target / "SHA256SUMS").write_text("\n".join(checksums) + "\n")
    archive = Path(str(target) + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(target, arcname=target.name)
    archive.with_name(archive.name + ".sha256").write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")
    print(archive)


if __name__ == "__main__":
    main()
