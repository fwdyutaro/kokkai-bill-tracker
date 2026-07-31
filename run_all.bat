@echo off
chcp 65001 >nul
rem 法律案トラッカー 週次高品質パイプライン（Ollama・e5・PDF抽出を使用）
setlocal
cd /d %~dp0
set PYTHONIOENCODING=utf-8
rem 対象会期は sessions.yaml の最新回次から取得する（取れなければ既定値のまま）。
set DIET=221
for /f "usebackq delims=" %%i in (`python sessions.py latest`) do set DIET=%%i
echo 対象会期: 第%DIET%回国会

echo [1/4] 議案ステータス収集・補強 ...
call :run collect.py python collect.py --diet %DIET% --sleep 1.0 --llm-summary
if errorlevel 1 exit /b %errorlevel%

echo [2/4] 参考文書クロール ...
call :run crawl.py python crawl.py
if errorlevel 1 exit /b %errorlevel%

echo [3/4] 参考リンク紐付け＋タグ付与 ...
call :run match_refs.py python match_refs.py
if errorlevel 1 exit /b %errorlevel%
call :run merge_submissions.py python merge_submissions.py
if errorlevel 1 exit /b %errorlevel%
call :run apply_suppressions.py python apply_suppressions.py
if errorlevel 1 exit /b %errorlevel%
call :run tag.py python tag.py
if errorlevel 1 exit /b %errorlevel%

echo [4/4] リンク死活チェック ...
python linkcheck.py --sample 80
set "LINKCHECK_RC=%ERRORLEVEL%"
if not "%LINKCHECK_RC%"=="0" (
  if "%STRICT_LINKCHECK%"=="1" (
    echo   ! linkcheck.py 失敗（終了コード %LINKCHECK_RC%）
    exit /b %LINKCHECK_RC%
  )
  echo   ! linkcheck.py 警告（終了コード %LINKCHECK_RC%）
)

echo 完了: data_collected.js を更新しました。
endlocal
exit /b 0

:run
set "STEP=%~1"
%2 %3 %4 %5 %6 %7 %8 %9
set "STEP_RC=%ERRORLEVEL%"
if not "%STEP_RC%"=="0" (
  echo   ! %STEP% 失敗（終了コード %STEP_RC%）
  exit /b %STEP_RC%
)
exit /b 0
