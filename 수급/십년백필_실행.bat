@echo off
chcp 65001 > nul
REM ==========================================================
REM  10년 월별 백필 - 전체 실행 (2016-01 ~ 2025-06, 약 1.5~2시간)
REM  중간에 끊겨도 다시 실행하면 체크포인트에서 이어받습니다.
REM  시험(십년백필_시험.bat) 통과 후 밤에 돌려두세요.
REM ==========================================================
set PYTHONUTF8=1
cd /d "%~dp0"

if "%KRX_ID%"=="" (
    echo KRX 정보데이터시스템 ^(data.krx.co.kr^) 계정을 입력하세요.
    set /p KRX_ID=KRX 아이디:
    set /p KRX_PW=KRX 비밀번호:
)

python pipeline\backfill.py

pause
