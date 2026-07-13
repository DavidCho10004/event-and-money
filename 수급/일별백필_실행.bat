@echo off
chcp 65001 > nul
REM ==========================================================
REM  일별 층 1년 백필 (첫 실행 시 자동으로 365일치 수집)
REM  소요: 약 1.5~2시간. 중간에 끊겨도 다시 실행하면 이어받습니다.
REM  KRX 로그인 세션이 1시간이라 중간에 만료로 멈출 수 있음 →
REM  그 경우 이 파일을 다시 실행하면 멈춘 날짜부터 계속됩니다.
REM ==========================================================
set PYTHONUTF8=1
cd /d "%~dp0"

if "%KRX_ID%"=="" (
    echo KRX 정보데이터시스템 ^(data.krx.co.kr^) 계정을 입력하세요.
    set /p KRX_ID=KRX 아이디:
    set /p KRX_PW=KRX 비밀번호:
)

pip install -q pyarrow

python pipeline\fetch_incremental.py

echo.
echo   마지막 줄이 FETCHED ... 이면 완료입니다.
pause
