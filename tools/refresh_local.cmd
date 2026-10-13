@echo off
rem Monthly local refresh: picks up sources that block GitHub's servers, re-imports
rem Carnegie and the Met, and pushes. Scheduled as the Windows task "OutTonightRefresh".
cd /d "%~dp0.."
git pull --rebase -X theirs origin main
python importers\carnegie.py
python importers\met_opera.py
python refresh.py
git add public seed_carnegie.json seed_met.json
git commit -m "local refresh %date%" || exit /b 0
git pull --rebase -X theirs origin main
git push origin main
