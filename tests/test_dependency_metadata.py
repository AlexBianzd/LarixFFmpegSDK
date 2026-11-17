import json,tempfile,unittest
from pathlib import Path
from scripts.common.release_manifest import generate_release_metadata,verify_release_metadata
from tests.test_manifest import create_sdk,LOCK,TARGET
ROOT=Path(__file__).resolve().parents[1]
class DependencyMetadataTests(unittest.TestCase):
 def test_manifest_declares_statically_included_dependencies(self):
  for profile,names in [('lgpl',['zimg']),('gpl',['x264','x265','zimg'])]:
   with self.subTest(profile=profile),tempfile.TemporaryDirectory() as td:
    sdk=Path(td)/'sdk';create_sdk(sdk,profile)
    manifest=generate_release_metadata(sdk,LOCK,profile,TARGET)
    self.assertEqual([d['name'] for d in manifest.get('externalDependencies',[])],names)
    sbom=json.loads((sdk/'share/larix-ffmpeg-sdk/sbom.spdx.json').read_text())
    included={p['name'] for p in sbom['packages']}
    self.assertTrue(set(names)<=included)
    if profile=='lgpl':self.assertFalse({'x264','x265'}&included)
 def test_tampered_dependency_license_is_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   sdk=Path(td)/'sdk';create_sdk(sdk,'gpl')
   license=sdk/'LICENSES/x265-COPYING'
   self.assertTrue(license.is_file(),'x265 license must accompany static code')
   generate_release_metadata(sdk,LOCK,'gpl',TARGET)
   license.write_bytes(b'wrong license')
   with self.assertRaises(ValueError):generate_release_metadata(sdk,LOCK,'gpl',TARGET)
