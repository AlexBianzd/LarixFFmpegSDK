import unittest
from pathlib import PureWindowsPath
import scripts.common.build_dependencies as build
class CMakePathTests(unittest.TestCase):
 def test_native_windows_paths_are_not_cmake_escape_sequences(self):
  normalize=getattr(build,'cmake_arguments',None)
  self.assertTrue(callable(normalize),'CMake argument normalization is missing')
  got=normalize([PureWindowsPath('C:/Program Files/CMake/bin/cmake.exe'),'-DZIMG_SOURCE=D:\\a\\sources\\zimg','-DCMAKE_CXX_FLAGS=/MT /pathmap:D:\\a\\sources=zimg-source'])
  self.assertEqual(got,['C:/Program Files/CMake/bin/cmake.exe','-DZIMG_SOURCE=D:/a/sources/zimg','-DCMAKE_CXX_FLAGS=/MT /pathmap:D:/a/sources=zimg-source'])
