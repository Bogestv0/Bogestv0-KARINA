@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo Установите Python 3.11 или 3.12 с python.org (галочка "Add to PATH"^) & pause & exit /b 1)
if not exist .venv (
  echo Первый запуск: создаю окружение и ставлю библиотеки, это займёт несколько минут...
  python -m venv .venv || (pause & exit /b 1)
  call .venv\Scripts\python -m pip install --upgrade pip
  call .venv\Scripts\python -m pip install -r requirements.txt || (echo Ошибка установки & pause & exit /b 1)
)
start "" .venv\Scripts\pythonw.exe main.py
