"""Build allowlisted, reproducible portable release archives without user data."""

import argparse
import ast
import gzip
import hashlib
import io
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import zipfile

ROOT=Path(__file__).resolve().parent.parent
PUBLIC_DIRS={'autoreserve','scripts','tools','tests','demo','docs','.github','config','native'}
PUBLIC_FILES={'README.md','README-CN.md','LICENSE','requirements.txt','pyproject.toml','MANIFEST.in',
              'run.sh','start-gui.sh','test.sh','.gitignore','.gitattributes'}
PRIVATE_PARTS={'.git','.auth','artifacts','.research','__pycache__','.venv','.runtime','build','dist'}


# ==========================================
# Function: Read the release version without importing application or GUI dependencies.
# Method: Parse the simple constant assignment in the package initializer.
# ==========================================
def version(root=ROOT):
    tree=ast.parse((Path(root)/'autoreserve/__init__.py').read_text())
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='__version__' for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('Missing application version')


# ==========================================
# Function: Select public source files independently of Git ignore status.
# Method: Enforce both top-level and private-path allowlists, including a narrow config exception.
# ==========================================
def public_files(root=ROOT):
    root=Path(root)
    result=[]
    candidates=[]
    for directory, folders, files in os.walk(root):
        folders[:]=sorted(d for d in folders if d not in PRIVATE_PARTS and not d.endswith('.egg-info')
                         and not (Path(directory)/d).is_symlink()
                         and (Path(directory)!=root or d in PUBLIC_DIRS))
        candidates.extend(Path(directory)/name for name in sorted(files))
    for path in sorted(candidates):
        if not path.is_file() or path.is_symlink():
            continue
        rel=path.relative_to(root)
        if any(part in PRIVATE_PARTS or part.endswith('.egg-info') for part in rel.parts):
            continue
        if len(rel.parts)==1:
            if rel.name not in PUBLIC_FILES:
                continue
        elif rel.parts[0] not in PUBLIC_DIRS:
            continue
        if rel.parts[0]=='config' and rel.as_posix() not in {'config/README.md','config/example.json','config/.gitignore'}:
            continue
        if rel.suffix in {'.pyc','.pyo','.log','.sqlite3','.tmp'}:
            continue
        result.append((rel,path))
    return result


# ==========================================
# Function: Build the mouse-clickable Linux entry point without requiring a compiler on end-user machines.
# Method: Compile the small launcher locally/CI and include its ELF only in architecture-specific portable bundles.
# ==========================================
def build_launcher(root=ROOT):
    root=Path(root)
    source=root/'native/launcher.c'
    if not source.exists():
        return None
    compiler=shutil.which('cc') or shutil.which('gcc')
    if not compiler:
        raise RuntimeError('Building Linux portable releases requires an existing C compiler.')
    staging=root/'.runtime/build'
    staging.mkdir(parents=True,exist_ok=True)
    temporary=staging/'AutoReserve'
    subprocess.run([compiler,'-std=c11','-Os','-Wall','-Wextra','-Werror','-Wl,-z,relro,-z,now',
                    str(source),'-o',str(temporary)],check=True,timeout=60)
    executable=root/'AutoReserve'
    shutil.copy2(temporary,executable)
    executable.chmod(0o755)
    return executable


# ==========================================
# Function: Package public program files into Linux portable tar and ZIP archives.
# Method: Normalize permissions/timestamps and explicitly exclude local configuration and authentication.
# ==========================================
def build(root=ROOT, output=None):
    root=Path(root)
    output=Path(output) if output else root/'dist'
    output.mkdir(parents=True,exist_ok=True)
    release=version(root)
    prefix=f'AutoReserve-{release}'
    entries=public_files(root)
    launcher=build_launcher(root)
    if launcher:
        entries.append((Path('AutoReserve'),launcher))
    epoch=int(os.environ.get('SOURCE_DATE_EPOCH','0'))
    target=f'{prefix}-linux-{platform.machine()}-portable'
    tar_path=output/f'{target}.tar.gz'
    with tar_path.open('wb') as file:
        with gzip.GzipFile(filename='',mode='wb',fileobj=file,mtime=epoch) as zipped:
            with tarfile.open(fileobj=zipped,mode='w') as archive:
                for relative,path in entries:
                    data=path.read_bytes()
                    info=tarfile.TarInfo(prefix+'/'+relative.as_posix())
                    info.size,info.mtime,info.mode=len(data),epoch,0o755 if relative.suffix=='.sh' or relative.name=='AutoReserve' else 0o644
                    archive.addfile(info,io.BytesIO(data))
    zip_path=output/f'{target}.zip'
    with zipfile.ZipFile(zip_path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for relative,path in entries:
            info=zipfile.ZipInfo(prefix+'/'+relative.as_posix(),(1980,1,1,0,0,0))
            info.create_system=3
            info.external_attr=(0o100755 if relative.suffix=='.sh' or relative.name=='AutoReserve' else 0o100644)<<16
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,path.read_bytes())
    return [tar_path,zip_path]


# ==========================================
# Function: Generate checksums only for public release assets.
# Method: Hash normalized archive/wheel outputs and write a standard SHA256SUMS file.
# ==========================================
def checksums(output):
    output=Path(output)
    assets=sorted(p for p in output.iterdir() if p.is_file() and p.name.endswith(('.tar.gz','.zip','.whl')))
    result=output/'SHA256SUMS'
    result.write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in assets))
    return result


# ==========================================
# Function: Build portable archives for local inspection or tagged CI release.
# Method: Reject a mismatched tag before creating artifacts and print only public output paths.
# ==========================================
def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'dist')
    parser.add_argument('--expected-tag')
    args=parser.parse_args(argv)
    if args.expected_tag and args.expected_tag!='v'+version():
        raise ValueError('Release tag must match v'+version())
    assets=build(output=args.output)
    assets.append(checksums(args.output))
    for path in assets:
        print(path)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
