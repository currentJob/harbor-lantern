@echo off
REM 프로젝트 품질 게이트 (release-engineering.md §2-1).
REM   gate            전 단계 실행 → 요약 한 줄
REM   gate --verbose  통과한 단계 출력까지
python "%~dp0gate.py" %*
