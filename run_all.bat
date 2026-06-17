@echo off
rem 法律案トラッカー 日次パイプライン（Windowsタスクスケジューラ向け）
rem collect(議案) と crawl(参考文書) を更新し、紐付けてサイト用データを再生成する。
setlocal
cd /d %~dp0
set PYTHONIOENCODING=utf-8
set DIET=221

echo [1/4] 議案ステータス収集・補強 ...
python collect.py --diet %DIET% --sleep 0.3
if errorlevel 1 echo   ! collect 失敗

echo [2/4] 参考文書クロール ...
python crawl.py
if errorlevel 1 echo   ! crawl 失敗

echo [3/4] 参考リンク紐付け ...
python match_refs.py
if errorlevel 1 echo   ! match 失敗

echo [4/4] リンク死活チェック ...
python linkcheck.py --sample 80

echo done. data_collected.js を更新しました。
endlocal
