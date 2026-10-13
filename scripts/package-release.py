#!/usr/bin/env python3
"""Create and verify the Installer/project ZIPs from the already built bundle.

Does not rebuild JavaScript. Run npm ci && npm run build first to perform a
normal complete source build. No settings or files outside output-dir are edited.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
EXCLUDED_DIRS={'.git','release','node_modules','__pycache__','.pytest_cache','.pnpm-store','obj','target','publish','publish_tmp'}
EXCLUDED_SUFFIXES={'.map','.pyc','.pyo','.tmp','.temp','.bak','.orig','.rej','.log'}
FONTS={'.ttf','.otf','.woff','.woff2','.ttc'}

def included(path: Path) -> bool:
    rel=path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS for part in rel.parts):return False
    if len(rel.parts)>1 and rel.parts[0].endswith('Bridge') and 'bin' in rel.parts[1:]:return False
    return path.is_file() and path.suffix.lower() not in EXCLUDED_SUFFIXES and path.name!='SOURCE_RECOVERY.md'

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def write_zip(destination: Path, files: list[Path], prefix: str) -> None:
    temporary=destination.with_suffix('.zip.tmp')
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for path in files:
                if path.suffix.lower() in FONTS:
                    raise RuntimeError(f'Refusing to include a font file: {path.name}')
                archive.write(path,prefix+'/'+path.relative_to(ROOT).as_posix())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'release')
    args=parser.parse_args()
    output=args.output_dir.expanduser().resolve()
    output.mkdir(parents=True,exist_ok=True)
    version=json.loads((ROOT/'package.json').read_text())['version']
    for filename in ('package-lock.json',):
        assert json.loads((ROOT/filename).read_text())['version']==version,filename
    runtime_names={'plugin.json','package.json','main.py','ytmusic_service.py','runtime_compat.py','LICENSE','NOTICE',f'CHANGELOG_{version}.md'}
    runtime_dirs={'dist','bin','vendor','licenses','runtime_stdlib'}
    required=[ROOT/name for name in runtime_names]+[ROOT/'dist/index.js',ROOT/'runtime_stdlib/http_server.py']
    for path in required:
        if not path.is_file():raise RuntimeError(f'Missing release file: {path}')
    project=sorted(p for p in ROOT.rglob('*') if included(p))
    runtime=[p for p in project if p.relative_to(ROOT).parts[0] in runtime_dirs or p.relative_to(ROOT).as_posix() in runtime_names]
    installer_zip=output/f'Now-Playing-Plus-decky_Installer-{version}.zip'
    project_zip=output/f'Now-Playing-Plus-project-{version}.zip'
    write_zip(installer_zip,runtime,'Now Playing Plus')
    write_zip(project_zip,project,'Now-Playing-Plus')
    with zipfile.ZipFile(installer_zip) as installer,zipfile.ZipFile(project_zip) as source:
        for archive in (installer,source):
            assert archive.testzip() is None,'ZIP CRC failure'
            assert len(archive.namelist())==len(set(archive.namelist())),'Duplicate archive entries'
            for name in archive.namelist():
                assert '..' not in Path(name).parts and not name.startswith('/'),name
        for name in installer.namelist():
            rel=name.removeprefix('Now Playing Plus/')
            assert installer.read(name)==source.read('Now-Playing-Plus/'+rel),rel
        for archive,prefix in ((installer,'Now Playing Plus'),(source,'Now-Playing-Plus')):
            assert json.loads(archive.read(prefix+'/package.json'))['version']==version
            assert prefix+'/.playhub-release.json' not in archive.namelist(),'fork must not ship a Playhub marker'
            assert 'runtime_compat' in archive.read(prefix+'/main.py').decode('utf-8')
    print(f'PASS: archive CRC, paths, metadata and {len(runtime)} identical runtime files; {len(project)} project files.')
    for path in (installer_zip,project_zip):
        print(f'{path.name}  {path.stat().st_size} bytes  SHA256 {sha(path.read_bytes())}')

if __name__=='__main__':main()
