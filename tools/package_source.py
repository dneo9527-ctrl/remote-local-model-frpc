#!/usr/bin/env python3
"""Build only explicit audited repository files, excluding installed state."""
from pathlib import Path
import argparse
import hashlib
import json
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def build(destination):
    metadata=json.loads((ROOT/'version.json').read_text(encoding='utf-8'))
    files=json.loads((ROOT/'source-files.json').read_text(encoding='utf-8'))
    if len(files)!=len(set(files)):
        raise ValueError('Duplicate allowlist entry')
    destination.mkdir(parents=True,exist_ok=True)
    stem='remote-local-model-frpc-'+metadata['version']
    output=destination/(stem+'-source.zip')
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as package:
        for name in files:
            path=ROOT/name
            if path.is_symlink() or not path.is_file() or ROOT not in path.resolve().parents:
                raise ValueError('Not a regular audited source file: '+name)
            mode=0o755 if name.endswith('.command') else 0o644
            info=zipfile.ZipInfo('remote-local-model-frpc/'+name,(2026,11,17,0,0,0))
            info.external_attr=(stat.S_IFREG|mode)<<16
            info.compress_type=zipfile.ZIP_DEFLATED
            package.writestr(info,path.read_bytes())
    checksum=hashlib.sha256(output.read_bytes()).hexdigest()
    (destination/(stem+'-SHA256SUMS.txt')).write_text(checksum+'  '+output.name+'\n',encoding='utf-8')
    print(json.dumps({'file':str(output),'sha256':checksum,'files':len(files)}))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'dist')
    build(parser.parse_args().out)
