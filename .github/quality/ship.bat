@echo off
REM 커밋~릴리스 일괄 (release-engineering.md §5.1-1).
REM   ship -m "메시지"
REM   ship -m "메시지" --tag v1.9
python "%~dp0ship.py" %*
