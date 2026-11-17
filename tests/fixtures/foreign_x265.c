/* Adversarial acceptance fixture: loading this unpinned encoder is a failure. */
#ifdef _WIN32
#include <windows.h>
BOOL WINAPI DllMain(HINSTANCE instance,DWORD reason,LPVOID reserved) {
  (void)instance;(void)reserved;
  if(reason==DLL_PROCESS_ATTACH){HANDLE h=CreateFileA("larix-unexpected-x265-load.marker",GENERIC_WRITE,FILE_SHARE_READ,NULL,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,NULL);if(h!=INVALID_HANDLE_VALUE)CloseHandle(h);}
  return TRUE;
}
__declspec(dllexport) int larix_foreign_marker(void){return 1;}
#else
#include <stdio.h>
__attribute__((constructor)) static void foreign_loaded(void){FILE *f=fopen("larix-unexpected-x265-load.marker","wb");if(f)fclose(f);}
int larix_foreign_marker(void){return 1;}
#endif
