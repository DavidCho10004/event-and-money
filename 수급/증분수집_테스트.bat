@echo off
chcp 65001 > nul
REM ==========================================================
REM  증분 수집 동작 검증 (최근 3거래일만 — 약 2~3분)
REM  본 수집(run_weekly.bat) 전에 파이프라인이 정상인지 확인용
REM ==========================================================
set PYTHONUTF8=1
cd /d "%~dp0"

if "%KRX_ID%"=="" (
    echo KRX 정보데이터시스템 ^(data.krx.co.kr^) 계정을 입력하세요.
    set /p KRX_ID=KRX 아이디:
    set /p KRX_PW=KRX 비밀번호:
)

pip install -q pyarrow

python pipeline\fetch_incremental.py --days 3

echo.
echo   위 마지막 줄이 FETCHED ... 이면 정상입니다.
pause
