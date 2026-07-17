@echo off
chcp 65001 > nul
REM ==========================================================
REM  일별 시가 보충 (1회성, 약 10분) - 단기 백테스트 전용
REM  중간에 끊겨도 다시 실행하면 이어받습니다.
REM ==========================================================
set PYTHONUTF8=1
cd /d "%~dp0"

if "%KRX_ID%"=="" (
    echo KRX 정보데이터시스템 ^(data.krx.co.kr^) 계정을 입력하세요.
    set /p KRX_ID=KRX 아이디:
    for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "$s=Read-Host 'KRX 비밀번호(입력 숨김)' -AsSecureString; $b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); [Runtime.InteropServices.Marshal]::PtrToStringAuto($b)"`) do set "KRX_PW=%%p"
)

python pipeline\fetch_daily_opens.py

echo.
echo   마지막 줄이 FILLED ... 이면 완료입니다.
pause
