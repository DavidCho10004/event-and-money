@echo off
chcp 65001 > nul
REM ==========================================================
REM  10년 월별 백필 - 시험 실행 (2016년 1~3월만, 약 5분)
REM  '사모' 등 과거 투자자 분류 존재 여부를 커버리지 리포트로 확인
REM ==========================================================
set PYTHONUTF8=1
cd /d "%~dp0"

if "%KRX_ID%"=="" (
    echo KRX 정보데이터시스템 ^(data.krx.co.kr^) 계정을 입력하세요.
    set /p KRX_ID=KRX 아이디:
    set /p KRX_PW=KRX 비밀번호:
)

python pipeline\backfill.py --start 201601 --end 201603

pause
