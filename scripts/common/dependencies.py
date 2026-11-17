"""Hash-locked external sources, built out of tree; never patch FFmpeg."""
from __future__ import annotations
import argparse
import copy
import posixpath
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
from scripts.common.model import _object_without_duplicates
from scripts.common.source import (_download, _is_link_like, _snapshot_verified_archive,
                                   _tree_manifest, _validated_members, _verify_archive)

_LOCK_DIGEST = '53433b16437b5abc1894d4651e52b20665c7db42cad9905178743fef9634508e'

def load_dependency_lock(repo_root: Path) -> dict:
    try:
        value=json.loads((repo_root/'config/dependencies.lock.json').read_text(encoding='utf-8'),object_pairs_hook=_object_without_duplicates)
    except (OSError,ValueError) as error:
        raise ValueError('invalid dependency lock') from error
    canonical=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    if hashlib.sha256(canonical).hexdigest()!=_LOCK_DIGEST:
        raise ValueError('dependency lock identity has drifted')
    return value

def selected_dependencies(repo_root: Path, profile: str) -> list[dict]:
    if profile not in {'lgpl','gpl'}:raise ValueError('invalid dependency profile')
    return [e for e in load_dependency_lock(repo_root)['dependencies'] if profile in e['profiles']]

def prepare_dependency(entry: dict, downloads: Path, destination: Path) -> Path:
    source=entry['source'];name=source['archive'];top=entry['root']
    if Path(name).name!=name or PurePosixPath(top).name!=top:
        raise ValueError('unsafe dependency identity')
    if _is_link_like(downloads) or _is_link_like(destination):raise ValueError('linked dependency directory')
    archive=downloads/name
    if not archive.exists() and not _is_link_like(archive):_download(source,archive)
    _verify_archive(source,archive)
    tree=destination/top;state=destination/'state.json'
    if destination.exists():
        if not state.is_file() or _is_link_like(state) or set(p.name for p in destination.iterdir())!={top,'state.json'}:
            raise ValueError('stale dependency source directory')
        accepted=json.loads(state.read_text(encoding='utf-8'))
        if accepted.get('identity')!=entry or accepted.get('tree')!=_tree_manifest(tree):
            raise ValueError('dependency source tree has drifted')
        return tree
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=Path(tempfile.mkdtemp(prefix='.dependency-',dir=destination.parent))
    try:
        snap=temporary/name;_snapshot_verified_archive(source,archive,snap)
        staging=temporary/'source';staging.mkdir()
        with tarfile.open(snap,'r:*') as tar:
            original=tar.getmembers();by_name={m.name:m for m in original};normalized=[];payloads={}
            for member in original:
                accepted=copy.copy(member)
                if member.issym():
                    target=posixpath.normpath(posixpath.join(posixpath.dirname(member.name),member.linkname))
                    if not target.startswith(top+'/') or target not in by_name or not by_name[target].isfile():
                        raise ValueError('unsafe dependency archive link')
                    payloads[member.name]=by_name[target]
                    accepted.type=tarfile.REGTYPE;accepted.size=by_name[target].size
                    accepted.mode=by_name[target].mode;accepted.linkname=''
                normalized.append(accepted)
            members=_validated_members(normalized,top)
            for member in members:
                path=staging.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():path.mkdir(parents=True,exist_ok=True);continue
                path.parent.mkdir(parents=True,exist_ok=True)
                payload=tar.extractfile(payloads.get(member.name,by_name[member.name]))
                if payload is None:raise ValueError('missing dependency payload')
                with payload,path.open('xb') as output:shutil.copyfileobj(payload,output)
                os.chmod(path,member.mode&0o777)
        if not (staging/top).is_dir():raise ValueError('missing dependency root')
        (staging/'state.json').write_text(json.dumps({'identity':entry,'tree':_tree_manifest(staging/top)},sort_keys=True,indent=2)+'\n',encoding='utf-8')
        os.replace(staging,destination)
    finally:shutil.rmtree(temporary,ignore_errors=True)
    return tree

def prepare_dependencies(repo_root: Path, output_root: Path, profile: str) -> dict[str,Path]:
    return {e['name']:prepare_dependency(e,output_root/'downloads',output_root/'sources'/e['name']) for e in selected_dependencies(repo_root,profile)}

def main() -> None:
    p=argparse.ArgumentParser();p.add_argument('--repo-root',type=Path,default=Path.cwd());p.add_argument('--output',type=Path,required=True);p.add_argument('--profile',choices=['lgpl','gpl'],required=True)
    a=p.parse_args();print(json.dumps({k:str(v.resolve()) for k,v in prepare_dependencies(a.repo_root.resolve(),a.output.resolve(),a.profile).items()},sort_keys=True))
if __name__=='__main__':main()

def dependency_records(repo: Path, profile: str) -> list[dict]:
    return [{**entry,'linkage':'static','features':({'bitDepths':[8,10],'chroma':'420'} if entry['name']=='x264' else {'bitDepths':[8,10],'hdr10plus':True} if entry['name']=='x265' else {'cpuOnly':True,'implementation':'scalar C++'})} for entry in selected_dependencies(repo,profile)]

def dependency_licenses(repo: Path, profile: str) -> dict[str,tuple[Path,str]]:
    result={}
    for entry in selected_dependencies(repo,profile):
        for relative,digest in entry['licenseFiles'].items():
            result[entry['name']+'-'+relative.replace('/','-')]=(repo/'licenses/dependencies'/entry['name']/relative.replace('/','-'),digest)
    return result

def stage_dependency_provenance(repo: Path, sdk: Path, profile: str) -> None:
    for name,(source,digest) in dependency_licenses(repo,profile).items():
        data=source.read_bytes()
        if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('dependency license identity mismatch')
        destination=sdk/'LICENSES'/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(data)
    destination=sdk/'share/larix-ffmpeg-sdk/provenance/config/dependencies.lock.json'
    destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes((repo/'config/dependencies.lock.json').read_bytes())

def verify_dependency_licenses(repo: Path, sdk: Path, profile: str) -> None:
    for name,(_,digest) in dependency_licenses(repo,profile).items():
        path=sdk/'LICENSES'/name
        if not path.is_file() or _is_link_like(path) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('SDK dependency license does not match locked source: '+name)
