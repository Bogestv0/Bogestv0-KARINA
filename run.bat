@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Нужен Python 3.11-3.13: для более новых версий у библиотек (onnxruntime, torch) ещё нет готовых сборок.
set "PY="
for %%V in (3.12 3.11 3.13) do if not defined PY (py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V")
if not defined PY (
  python -c "import sys; sys.exit(0 if (3,11)<=sys.version_info[:2]<=(3,13) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Не найден подходящий Python. Нужен Python 3.12 (подойдёт 3.11-3.13^), а у вас либо его нет, либо стоит более новый.
  echo Скачайте Python 3.12 с python.org/downloads (галочка "Add python.exe to PATH"^) и запустите run.bat ещё раз.
  echo Новый Python можно не удалять: рядом спокойно живут несколько версий.
  pause & exit /b 1
)
rem Окружение, созданное неподходящей версией (например, 3.15), пересоздаём
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -c "import sys; sys.exit(0 if (3,11)<=sys.version_info[:2]<=(3,13) else 1)" >nul 2>nul || (
    echo Старое окружение создано неподходящей версией Python, создаю заново...
    rmdir /s /q .venv
  )
)
if not exist .venv (
  echo Первый запуск: создаю окружение и ставлю библиотеки, это займёт несколько минут...
  %PY% -m venv .venv || (pause & exit /b 1)
  call .venv\Scripts\python -m pip install --upgrade pip
)
rem Список библиотек изменился (например, в обновлении появился torch для голоса Silero) — доустанавливаем сами
fc /b requirements.txt .venv\requirements.installed.txt >nul 2>nul
if errorlevel 1 (
  echo Ставлю библиотеки (torch для естественного голоса - около 200 МБ, один раз^)...
  call .venv\Scripts\python -m pip install -r requirements.txt || (echo Ошибка установки. Пришлите текст выше. & pause & exit /b 1)
  copy /y requirements.txt .venv\requirements.installed.txt >nul
)
start "" .venv\Scripts\pythonw.exe main.py
