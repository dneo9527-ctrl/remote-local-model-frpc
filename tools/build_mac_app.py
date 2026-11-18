#!/usr/bin/env python3
"""Build a native menu app in a NEW directory; never installs or changes login items.

Paths in MenuSettings.json contain no keys. The private console settings stay in
their existing location; the menu reads only its local instance identity.
"""
from pathlib import Path
import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--python', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--launcher-source', type=Path, default=ROOT)
    parser.add_argument('--icon', type=Path)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('This builder needs macOS AppKit and an existing Swift compiler.')
    output = args.out.expanduser().resolve()
    if output.exists() or output.suffix != '.app':
        parser.error('--out must be a new .app path; existing apps are never overwritten')
    cfg = json.loads(args.config.expanduser().read_text(encoding='utf-8'))
    runtime = Path(cfg['runtime']).expanduser().resolve()
    paths = {key: str(value.expanduser().resolve()) for key, value in {
        'python': args.python, 'source': args.launcher_source,
        'config': args.config, 'state': args.state,
    }.items()}
    if not Path(paths['python']).is_file() or not (Path(paths['source']) / 'tools/launch.py').is_file():
        parser.error('Existing Python and console source paths are required.')
    version = json.loads((ROOT/'version.json').read_text())['version']
    resources = output/'Contents/Resources'
    executable = output/'Contents/MacOS/LocalAIConsole'
    resources.mkdir(parents=True, mode=0o700)
    executable.parent.mkdir(mode=0o700)
    try:
        subprocess.run(['/usr/bin/xcrun', 'swiftc', '-swift-version', '5', '-O',
                        '-framework', 'AppKit', str(ROOT/'mac/menu/LocalAIConsole.swift'),
                        '-o', str(executable)], check=True)
        info = {'CFBundleIdentifier': 'local.ai.console',
                'CFBundleName': '本地 AI 控制台', 'CFBundleDisplayName': '本地 AI 控制台',
                'CFBundleExecutable': 'LocalAIConsole', 'CFBundlePackageType': 'APPL',
                'CFBundleShortVersionString': version.split('-')[0],
                'CFBundleVersion': version.split('-preview.')[-1] if '-preview.' in version else '1',
                'LocalAIConsoleSourceVersion': version, 'LSUIElement': True,
                'NSHighResolutionCapable': True}
        if args.icon:
            shutil.copy2(args.icon, resources/'AppIcon.icns')
            info['CFBundleIconFile'] = 'AppIcon.icns'
        (output/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        paths.update(runtime=str(runtime), port=cfg.get('port', 18765))
        settings = resources/'MenuSettings.json'
        settings.write_text(json.dumps(paths, ensure_ascii=False, indent=2)+'\n')
        settings.chmod(0o600)
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-',
                        '--identifier', 'local.ai.console', str(output)], check=True)
        subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(output)], check=True)
    except Exception:
        # Keep partial output for diagnostics; never change any installed app.
        raise SystemExit('Build failed; partial NEW output retained. Installed app unchanged.') from None
    print(json.dumps({'app': str(output), 'version': version,
                      'native_menu': True, 'installed': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
