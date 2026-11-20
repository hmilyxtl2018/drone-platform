@echo off
cd /d "%~dp0"
echo open http://127.0.0.1:8765/agents.html?api
echo      http://127.0.0.1:8765/kb.html
echo      http://127.0.0.1:8765/space.html
echo      http://127.0.0.1:8765/cop.html
python serve.py
