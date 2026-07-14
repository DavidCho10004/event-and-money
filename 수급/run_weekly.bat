@echo off
chcp 65001 > nul
REM ==========================================================
REM  주간 수급 갱신 (매주 금요일 저녁)
REM  [1/5] 증분 수집 → [2/5] 검증 → [3/5] 분석 → [4/5] 커밋 → [5/5] 푸시
REM  마지막 줄:  ✅ VALIDATED & PUSHED  또는  ❌ 에러 (로그 참조)
REM ==========================================================
set PYTHONUTF8=1
cd /d "%~dp0"

if not exist logs mkdir logs
set LOG=logs\run_weekly_%date:~0,4%%date:~5,2%%date:~8,2%.log
echo ===== run_weekly %date% %time% ===== >> "%LOG%"

if "%KRX_ID%"=="" (
    echo KRX 정보데이터시스템 ^(data.krx.co.kr^) 계정을 입력하세요.
    set /p KRX_ID=KRX 아이디:
    for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "$s=Read-Host 'KRX 비밀번호(입력 숨김)' -AsSecureString; $b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); [Runtime.InteropServices.Marshal]::PtrToStringAuto($b)"`) do set "KRX_PW=%%p"
)

echo [1/5] 증분 수집 (지난 실행 이후 거래일)...
python pipeline\fetch_incremental.py >> "%LOG%" 2>&1
if errorlevel 1 goto :fail_fetch

echo [2/5] 데이터 검증 (validate)...
python pipeline\validate.py >> "%LOG%" 2>&1
if errorlevel 1 goto :fail_validate

echo [3/5] 스크리너/상세 데이터 생성 (analyze)...
python -W ignore pipeline\analyze.py >> "%LOG%" 2>&1
if errorlevel 1 goto :fail_analyze

echo [4/5] git 커밋...
cd ..
git add data/processed >> "수급\%LOG%" 2>&1
git diff --cached --quiet && goto :nothing
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i
git commit -m "data: 주간 수급 갱신 (%TODAY%)" >> "수급\%LOG%" 2>&1
if errorlevel 1 goto :fail_git

echo [5/5] git push (Railway 자동 배포)...
git push origin master >> "수급\%LOG%" 2>&1
if errorlevel 1 goto :fail_git
cd 수급

echo.
echo ✅ VALIDATED ^& PUSHED  (1~2분 후 운영 반영)
goto :end

:nothing
cd 수급
echo.
echo ✅ VALIDATED (변경 없음 — push 생략)
goto :end

:fail_fetch
echo ❌ 수집 실패 — 로그: %LOG%
goto :end
:fail_validate
echo ❌ 검증 실패 — push 차단됨. 로그: %LOG%
goto :end
:fail_analyze
echo ❌ 분석 실패 — push 차단됨. 로그: %LOG%
goto :end
:fail_git
cd 수급 2>nul
echo ❌ git 커밋/푸시 실패 — 로그: %LOG%
goto :end

:end
pause
