from __future__ import annotations
import copy, hashlib, importlib, io, json, tempfile, tarfile, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
try:
    deps = importlib.import_module('scripts.common.dependencies')
except ModuleNotFoundError:
    deps = None

class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(deps, 'verified dependency acquisition is not implemented')
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
    def fixture(self, name='source/file', content=b'original', link=False):
        path=self.root/'fixture.tar.gz'
        with tarfile.open(path,'w:gz') as t:
            info=tarfile.TarInfo(name)
            if link: info.type=tarfile.SYMTYPE;info.linkname='../../escape'
            else: info.size=len(content)
            t.addfile(info,None if link else io.BytesIO(content))
        source={'url':'https://example.invalid/fixed.tar.gz','archive':path.name,'size':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        entry={'name':'fixture','root':'source','source':source}
        cache=self.root/'cache';cache.mkdir();(cache/path.name).write_bytes(path.read_bytes())
        return entry,cache
    def test_lgpl_selects_only_zimg(self):
        self.assertEqual([d['name'] for d in deps.selected_dependencies(ROOT,'lgpl')],['zimg'])
        self.assertEqual([d['name'] for d in deps.selected_dependencies(ROOT,'gpl')],['x264','x265','zimg'])
        with self.assertRaises(ValueError):deps.selected_dependencies(ROOT,'nonfree')
    def test_lock_rejects_identity_drift_and_duplicate_members(self):
        data=json.loads((ROOT/'config/dependencies.lock.json').read_text());(self.root/'config').mkdir()
        p=self.root/'config/dependencies.lock.json'
        for candidate in [dict(data, schemaVersion=True),dict(data, extra=True)]:
            p.write_text(json.dumps(candidate))
            with self.assertRaises(ValueError):deps.load_dependency_lock(self.root)
        changed=copy.deepcopy(data);changed['dependencies'][0]['source']['sha256']='0'*64;p.write_text(json.dumps(changed))
        with self.assertRaises(ValueError):deps.load_dependency_lock(self.root)
        p.write_text((ROOT/'config/dependencies.lock.json').read_text().replace('"schemaVersion": 1','"schemaVersion": 1, "schemaVersion": 1'))
        with self.assertRaises(ValueError):deps.load_dependency_lock(self.root)
    def test_extract_and_reuse_verifies_complete_source_tree(self):
        entry,cache=self.fixture();dest=self.root/'prepared'
        source=deps.prepare_dependency(entry,cache,dest)
        self.assertEqual((source/'file').read_bytes(),b'original')
        self.assertEqual(deps.prepare_dependency(entry,cache,dest),source)
        (source/'file').write_bytes(b'tampered')
        with self.assertRaises(ValueError):deps.prepare_dependency(entry,cache,dest)
    def test_internal_doc_links_are_safely_materialized(self):
        entry,cache=self.fixture()
        path=cache/'fixture.tar.gz'
        with tarfile.open(path,'w:gz') as tar:
            file=tarfile.TarInfo('source/file');file.size=8;tar.addfile(file,io.BytesIO(b'original'))
            link=tarfile.TarInfo('source/docs/link');link.type=tarfile.SYMTYPE;link.linkname='../file';tar.addfile(link)
        entry['source'].update(size=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        source=deps.prepare_dependency(entry,cache,self.root/'prepared')
        self.assertEqual((source/'docs/link').read_bytes(),b'original')
        self.assertFalse((source/'docs/link').is_symlink())
    def test_rejects_tampered_archive_before_extraction(self):
        entry,cache=self.fixture();(cache/'fixture.tar.gz').write_bytes(b'tampered')
        with self.assertRaises(ValueError):deps.prepare_dependency(entry,cache,self.root/'prepared')
        self.assertFalse((self.root/'prepared').exists())
    def test_rejects_traversal_and_links(self):
        for name,link in [('source/../escape',False),('source/link',True)]:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as td:
                old=self.root;self.root=Path(td);entry,cache=self.fixture(name,link=link)
                with self.assertRaises(ValueError):deps.prepare_dependency(entry,cache,self.root/'prepared')
                self.assertFalse((self.root/'escape').exists());self.root=old
    def test_lgpl_never_fetches_gpl_archives(self):
        # Mock only external network/preparation; selection is real.
        with mock.patch.object(deps,'prepare_dependency',return_value=self.root) as prepare:
            got=deps.prepare_dependencies(ROOT,self.root,'lgpl')
        self.assertEqual(set(got),{'zimg'})
        self.assertEqual([call.args[0]['name'] for call in prepare.call_args_list],['zimg'])

if __name__=='__main__':unittest.main()
