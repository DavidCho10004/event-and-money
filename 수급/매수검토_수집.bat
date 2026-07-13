@echo off
chcp 65001 > nul
REM ==========================================================
REM  매수 검토 매트릭스 수집 (친구분 엑셀 컨셉)
REM  최신 pykrx는 KRX 정보데이터시스템 로그인이 필수입니다.
REM  계정이 없으면 먼저 data.krx.co.kr 에서 무료 회원가입 하세요.
REM  결과: data\매수검토_KOSPI.csv, data\매수검토_KOSDAQ.csv
REM ==========================================================
set PYTHONUTF8=1
cd /d "%~dp0"

if "%KRX_ID%"=="" (
    echo KRX 정보데이터시스템 ^(data.krx.co.kr^) 계정을 입력하세요.
    set /p KRX_ID=KRX 아이디:
    set /p KRX_PW=KRX 비밀번호:
)

pip install -q --upgrade pykrx

python app\fetch_buy_review.py --market ALL

echo.
echo   완료. data 폴더의 매수검토_*.csv 를 확인하세요.
pause
