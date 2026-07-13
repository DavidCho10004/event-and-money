@echo off
chcp 65001 > nul
REM ==========================================================
REM  매수 검토 매트릭스 수집 (친구분 엑셀 컨셉)
REM  더블클릭하면 코스피+코스닥 전 종목을 수집합니다.
REM  소요 시간: 약 5~10분 (KRX 데이터)
REM  결과: data\매수검토_KOSPI.csv, data\매수검토_KOSDAQ.csv
REM ==========================================================
set PYTHONUTF8=1
cd /d "%~dp0"

pip install -q pykrx

python app\fetch_buy_review.py --market ALL

echo.
echo   완료. data 폴더의 매수검토_*.csv 를 확인하세요.
pause
