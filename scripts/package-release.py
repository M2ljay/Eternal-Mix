#!/usr/bin/env python3
"""Create and verify the Installer/project ZIPs from the already built bundle.

The installer zip carries install-manifest.json (path, size and SHA-256 of every
other file; mix_install_check), written first. Entries are ordered so that if
Decky's ZipFile.extractall stops on a file in use, the manifest, the install
check and main.py are already in place and bin/ (the files most likely to be in
use) comes last.

Does not rebuild JavaScript. Run npm ci && npm run build first to perform a
normal complete source build. No settings or files outside output-dir are edited.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import mix_install_check  # noqa: E402
# The installer's top folder becomes the Decky plugin folder, which also names
# the plugin's settings folder (homebrew/settings/<folder>).
PLUGIN_FOLDER=json.loads((ROOT/'plugin.json').read_text(encoding='utf-8'))['name']
PROJECT_FOLDER='Eternal-Mix'
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

# Written first, in this order, ahead of everything else.
FIRST=('mix_install_check.py','mix_process_guard.py','main.py','plugin.json','package.json')
# Written last: executables a running helper may hold open.
LAST_DIRS=('bin',)

def install_order(rel: str) -> tuple:
    parts=rel.split('/')
    if rel in FIRST:return (0,FIRST.index(rel),rel)
    if parts[0] in LAST_DIRS:return (3,0,rel)
    return (1 if len(parts)==1 else 2,0,rel)

def write_zip(destination: Path, files: list[Path], prefix: str, manifest: bytes|None=None) -> None:
    temporary=destination.with_suffix('.zip.tmp')
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            if manifest is not None:
                archive.writestr(prefix+'/'+mix_install_check.MANIFEST_NAME,manifest)
            for path in sorted(files,key=lambda item:install_order(item.relative_to(ROOT).as_posix())):
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
    runtime_names={'plugin.json','package.json','main.py','mix_backend.py','mix_install_check.py','mix_process_guard.py','ytmusic_service.py','runtime_compat.py','legacy_migration.py','mix_identity.py','mix_update.py','LICENSE','NOTICE',f'CHANGELOG_{version}.md'}
    runtime_dirs={'dist','bin','vendor','licenses','runtime_stdlib'}
    required=[ROOT/name for name in runtime_names]+[ROOT/'dist/index.js',ROOT/'runtime_stdlib/http_server.py']
    for path in required:
        if not path.is_file():raise RuntimeError(f'Missing release file: {path}')
    project=sorted(p for p in ROOT.rglob('*') if included(p))
    runtime=[p for p in project if p.relative_to(ROOT).parts[0] in runtime_dirs or p.relative_to(ROOT).as_posix() in runtime_names]
    installer_zip=output/f'Eternal-Mix-decky_Installer-{version}.zip'
    project_zip=output/f'Eternal-Mix-project-{version}.zip'
    manifest=mix_install_check.build_manifest(PLUGIN_FOLDER,version,((p.relative_to(ROOT).as_posix(),p.read_bytes()) for p in runtime))
    write_zip(installer_zip,runtime,PLUGIN_FOLDER,mix_install_check.encode_manifest(manifest))
    write_zip(project_zip,project,PROJECT_FOLDER)
    with zipfile.ZipFile(installer_zip) as installer,zipfile.ZipFile(project_zip) as source:
        for archive in (installer,source):
            assert archive.testzip() is None,'ZIP CRC failure'
            assert len(archive.namelist())==len(set(archive.namelist())),'Duplicate archive entries'
            for name in archive.namelist():
                assert '..' not in Path(name).parts and not name.startswith('/'),name
        names=installer.namelist()
        manifest_entry=PLUGIN_FOLDER+'/'+mix_install_check.MANIFEST_NAME
        assert names[0]==manifest_entry,'install-manifest.json must be the first entry'
        assert [n.removeprefix(PLUGIN_FOLDER+'/') for n in names[1:1+len(FIRST)]]==list(FIRST),names[:6]
        first_last=min(i for i,n in enumerate(names) if n.removeprefix(PLUGIN_FOLDER+'/').split('/')[0] in LAST_DIRS)
        assert all(n.removeprefix(PLUGIN_FOLDER+'/').split('/')[0] in LAST_DIRS for n in names[first_last:]),'bin/ must come last'
        listed=mix_install_check.decode_manifest(installer.read(manifest_entry))
        assert listed['version']==version and len(listed['files'])==len(names)-1,'manifest does not list every file'
        for name in names[1:]:
            rel=name.removeprefix(PLUGIN_FOLDER+'/')
            assert installer.read(name)==source.read(PROJECT_FOLDER+'/'+rel),rel
        for archive,prefix in ((installer,PLUGIN_FOLDER),(source,PROJECT_FOLDER)):
            assert json.loads(archive.read(prefix+'/package.json'))['version']==version
            assert prefix+'/.playhub-release.json' not in archive.namelist(),'fork must not ship a Playhub marker'
            assert 'import mix_install_check' in archive.read(prefix+'/main.py').decode('utf-8')
            assert 'runtime_compat' in archive.read(prefix+'/mix_backend.py').decode('utf-8')
    print(f'PASS: archive CRC, paths, metadata and {len(runtime)} identical runtime files; {len(project)} project files.')
    for path in (installer_zip,project_zip):
        print(f'{path.name}  {path.stat().st_size} bytes  SHA256 {sha(path.read_bytes())}')

if __name__=='__main__':main()
