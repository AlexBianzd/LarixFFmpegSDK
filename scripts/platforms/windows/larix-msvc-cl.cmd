@echo off
setlocal
if not defined LARIX_REAL_CL goto missing_real_cl
if "%~1"=="" if "%~2"=="" goto identity
if "%~1"=="-nologo-" if "%~2"=="" goto identity
"%LARIX_REAL_CL%" %*
exit /b %ERRORLEVEL%

:identity
if not defined LARIX_MSVC_IDENTITY goto missing_identity
echo %LARIX_MSVC_IDENTITY%
exit /b 0

:missing_real_cl
echo LARIX_REAL_CL is required. 1>&2
exit /b 2

:missing_identity
echo LARIX_MSVC_IDENTITY is required for the FFmpeg MSVC probe. 1>&2
exit /b 2
