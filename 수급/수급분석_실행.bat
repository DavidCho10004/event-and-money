@echo off
chcp 65001 > nul
REM ==========================================================
REM  코스피/코스닥 투자자별 수급 분석 - 실행 스크립트
REM  더블클릭하면 브라우저에서 앱이 열립니다.
REM ==========================================================
set PYTHONUTF8=1
cd /d "%~dp0"

echo.
echo   수급 분석 앱을 시작합니다...
echo   브라우저가 자동으로 열립니다. (닫으려면 이 창에서 Ctrl+C)
echo.

python -m streamlit run app\streamlit_app.py

pause
