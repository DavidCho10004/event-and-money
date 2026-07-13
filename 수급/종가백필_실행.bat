@echo off
chcp 65001 > nul
REM ==========================================================
REM  과거 일별 종가/시총/상장주식수 보충 (1회성, 약 15분)
REM  중간에 끊겨도 다시 실행하면 이어받습니다.
REM ==========================================================
set PYTHONUTF8=1
cd /d "%~dp0"

if "%KRX_ID%"=="" (
    echo KRX 정보데이터시스템 ^(data.krx.co.kr^) 계정을 입력하세요.
    set /p KRX_ID=KRX 아이디:
    set /p KRX_PW=KRX 비밀번호:
)

python pipeline\fetch_daily_caps.py

echo.
echo   마지막 줄이 FILLED ... 이면 완료입니다.
pause
