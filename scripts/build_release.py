from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path
from textwrap import dedent
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / 'dist'
BUILD_DIR = ROOT / 'build'
RELEASE_DIR = DIST_DIR / 'release'
APP_NAME = 'CHISP Flasher'
APP_ID = 'chisp-flasher'
PUBLISHER = 'Paweł Jarczak'
PUBLISHER_URL = 'https://github.com/jarczakpawel/'
DESCRIPTION = 'Cross-platform ISP flasher for WCH CH32, CH5x and CH6x devices'


def read_version() -> str:
    with (ROOT / 'pyproject.toml').open('rb') as fh:
        data = tomllib.load(fh)
    return str(data['project']['version']).strip()


def version_tag() -> str:
    raw = os.environ.get('GITHUB_REF_NAME', '').strip()
    if raw:
        return raw
    version = read_version()
    return version if version.startswith('v') else f'v{version}'


def version_plain() -> str:
    tag = version_tag()
    return tag[1:] if tag.startswith('v') else tag


def platform_name() -> str:
    if sys.platform == 'win32':
        return 'windows'
    if sys.platform == 'darwin':
        return 'macos'
    return 'linux'


def arch_name() -> str:
    value = platform.machine().lower()
    if value in {'x86_64', 'amd64'}:
        return 'x64'
    if value in {'aarch64', 'arm64'}:
        return 'arm64'
    return value or 'unknown'


def deb_arch_name() -> str:
    value = arch_name()
    if value == 'x64':
        return 'amd64'
    if value == 'arm64':
        return 'arm64'
    return value


def rpm_arch_name() -> str:
    value = arch_name()
    if value == 'x64':
        return 'x86_64'
    if value == 'arm64':
        return 'aarch64'
    return value


def data_sep() -> str:
    return ';' if sys.platform == 'win32' else ':'


def add_data_arg(src: Path, dst: str) -> str:
    return f'{src}{data_sep()}{dst}'


def ensure_clean_dir(path: Path) -> Path:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8', newline='\n')
    if executable:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, symlinks=True)


def icon_path() -> Path:
    if sys.platform == 'win32':
        return ROOT / 'packaging' / 'icons' / 'app.ico'
    if sys.platform == 'darwin':
        return ROOT / 'packaging' / 'icons' / 'app.icns'
    return ROOT / 'src' / 'chisp_flasher' / 'ui' / 'assets' / 'app_icon.png'


def pyinstaller_cmd() -> list[str]:
    return [
        sys.executable,
        '-m',
        'PyInstaller',
        '--noconfirm',
        '--clean',
        '--windowed',
        '--name',
        APP_NAME,
        '--paths',
        str(ROOT / 'src'),
        '--icon',
        str(icon_path()),
        '--add-data',
        add_data_arg(ROOT / 'src' / 'chisp_flasher' / 'data', 'chisp_flasher/data'),
        '--add-data',
        add_data_arg(ROOT / 'src' / 'chisp_flasher' / 'ui' / 'assets', 'chisp_flasher/ui/assets'),
        str(ROOT / 'src' / 'chisp_flasher' / 'app' / 'main.py'),
    ]


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=str(cwd or ROOT), check=True)


def built_output_path() -> Path:
    if sys.platform == 'darwin':
        path = DIST_DIR / f'{APP_NAME}.app'
        if not path.exists():
            raise FileNotFoundError(path)
        return path
    path = DIST_DIR / APP_NAME
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def release_basename() -> str:
    return f'{APP_ID}-{version_tag()}-{platform_name()}-{arch_name()}'


def create_portable_bundle() -> Path:
    bundle_root = ensure_clean_dir(BUILD_DIR / 'portable' / release_basename())
    built = built_output_path()
    copy_tree(built, bundle_root / built.name)
    shutil.copy2(ROOT / 'README.md', bundle_root / 'README.md')
    if (ROOT / 'CHANGELOG.md').exists():
        shutil.copy2(ROOT / 'CHANGELOG.md', bundle_root / 'CHANGELOG.md')
    if (ROOT / 'reference').exists():
        copy_tree(ROOT / 'reference', bundle_root / 'reference')
    if (ROOT / 'assets' / 'readme').exists():
        copy_tree(ROOT / 'assets' / 'readme', bundle_root / 'assets' / 'readme')
    if sys.platform.startswith('linux'):
        shutil.copy2(ROOT / 'packaging' / 'linux' / '50-chisp-flasher.rules', bundle_root / '50-chisp-flasher.rules')
    return bundle_root


def zip_dir(src: Path, dst: Path) -> None:
    ensure_parent(dst)
    with ZipFile(dst, 'w', compression=ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(src.rglob('*')):
            if path.is_dir():
                continue
            zf.write(path, path.relative_to(src.parent))


def tar_gz_dir(src: Path, dst: Path) -> None:
    ensure_parent(dst)
    with tarfile.open(dst, 'w:gz') as tf:
        tf.add(src, arcname=src.name, recursive=True)


def build_portable_archive() -> None:
    bundle_root = create_portable_bundle()
    name = release_basename()
    if sys.platform == 'win32':
        zip_dir(bundle_root, RELEASE_DIR / f'{name}-portable.zip')
        return
    if sys.platform == 'darwin':
        run([
            'ditto',
            '-c',
            '-k',
            '--sequesterRsrc',
            '--keepParent',
            str(bundle_root),
            str(RELEASE_DIR / f'{name}-portable.zip'),
        ])
        return
    tar_gz_dir(bundle_root, RELEASE_DIR / f'{name}-portable.tar.gz')


def stage_linux_package_root() -> Path:
    stage = ensure_clean_dir(BUILD_DIR / 'linux-package-root')
    built = built_output_path()
    app_dir = stage / 'opt' / APP_ID / 'app'
    copy_tree(built, app_dir)

    launcher = dedent('''\
        #!/usr/bin/env sh
        set -e
        exec "/opt/chisp-flasher/app/CHISP Flasher" "$@"
    ''')
    write_text(stage / 'usr' / 'bin' / APP_ID, launcher, executable=True)

    desktop_src = ROOT / 'packaging' / 'linux' / 'chisp-flasher.desktop'
    desktop_dst = stage / 'usr' / 'share' / 'applications' / desktop_src.name
    ensure_parent(desktop_dst)
    shutil.copy2(desktop_src, desktop_dst)

    readme_dst = stage / 'usr' / 'share' / 'doc' / APP_ID / 'README.md'
    ensure_parent(readme_dst)
    shutil.copy2(ROOT / 'README.md', readme_dst)

    rules_dst = stage / 'usr' / 'lib' / 'udev' / 'rules.d' / '50-chisp-flasher.rules'
    ensure_parent(rules_dst)
    shutil.copy2(ROOT / 'packaging' / 'linux' / '50-chisp-flasher.rules', rules_dst)

    icon_dir = ROOT / 'packaging' / 'icons' / 'png'
    for size in [16, 24, 32, 48, 64, 128, 256, 512]:
        src = icon_dir / f'app_icon_{size}.png'
        if not src.exists():
            continue
        dst = stage / 'usr' / 'share' / 'icons' / 'hicolor' / f'{size}x{size}' / 'apps' / f'{APP_ID}.png'
        ensure_parent(dst)
        shutil.copy2(src, dst)

    return stage


def build_linux_packages() -> None:
    stage = stage_linux_package_root()
    common = [
        'fpm',
        '-s', 'dir',
        '-C', str(stage),
        '-n', APP_ID,
        '-v', version_plain(),
        '--description', DESCRIPTION,
        '--maintainer', PUBLISHER,
        '--vendor', PUBLISHER,
        '--url', PUBLISHER_URL,
        '--category', 'Development/Utilities',
        '--after-install', str(ROOT / 'packaging' / 'linux' / 'post_install.sh'),
        '--after-remove', str(ROOT / 'packaging' / 'linux' / 'post_remove.sh'),
    ]

    run(common + [
        '-t', 'deb',
        '--architecture', deb_arch_name(),
        '-p', str(RELEASE_DIR / f'{release_basename()}.deb'),
    ])

    run(common + [
        '-t', 'rpm',
        '--architecture', rpm_arch_name(),
        '-p', str(RELEASE_DIR / f'{release_basename()}.rpm'),
    ])


def find_iscc() -> str:
    exe = shutil.which('ISCC.exe')
    if exe:
        return exe
    exe = shutil.which('iscc')
    if exe:
        return exe
    default = Path(r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe')
    if default.exists():
        return str(default)
    raise FileNotFoundError('ISCC.exe not found')


def build_windows_installer() -> None:
    iscc = find_iscc()
    output_name = f'{release_basename()}-setup'
    run([
        iscc,
        f'/DMyAppVersion={version_plain()}',
        f'/DSourceDir={DIST_DIR / APP_NAME}',
        f'/DOutputDir={RELEASE_DIR}',
        f'/DOutputBaseFilename={output_name}',
        f'/DSetupIconFile={ROOT / "packaging" / "icons" / "app.ico"}',
        str(ROOT / 'packaging' / 'windows' / 'chisp-flasher.iss'),
    ])


def build_macos_dmg() -> None:
    app_path = built_output_path()
    stage = ensure_clean_dir(BUILD_DIR / 'macos-dmg')
    copy_tree(app_path, stage / app_path.name)
    applications_link = stage / 'Applications'
    if applications_link.exists() or applications_link.is_symlink():
        applications_link.unlink()
    os.symlink('/Applications', applications_link)
    run([
        'hdiutil',
        'create',
        '-volname', APP_NAME,
        '-srcfolder', str(stage),
        '-ov',
        '-format', 'UDZO',
        str(RELEASE_DIR / f'{release_basename()}.dmg'),
    ])


def main() -> int:
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    shutil.rmtree(RELEASE_DIR, ignore_errors=True)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)

    run(pyinstaller_cmd())
    build_portable_archive()

    if sys.platform.startswith('linux'):
        build_linux_packages()
    elif sys.platform == 'win32':
        build_windows_installer()
    elif sys.platform == 'darwin':
        build_macos_dmg()

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
