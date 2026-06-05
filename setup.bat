@echo off
setlocal enabledelayedexpansion
title Harness Setup

echo.
echo ╔══════════════════════════════════════════════╗
echo ║       H A R N E S S   S E T U P             ║
echo ║       AI Coding Agent CLI                    ║
echo ╚══════════════════════════════════════════════╝
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.12+ from https://python.org
    echo         Make sure "Add Python to PATH" is checked during installation.
    pause
    exit /b 1
)
echo [OK] Python found:
python --version

:: Step 1: Create virtual environment
echo.
echo [1/3] Creating virtual environment...
if exist .venv (
    echo       .venv already exists, skipping.
) else (
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)
echo [OK] Virtual environment ready.

:: Step 2: Install harness
echo.
echo [2/3] Installing harness and dependencies...
call .venv\Scripts\activate.bat
pip install -e . --quiet
if %errorlevel% neq 0 (
    echo [ERROR] Installation failed.
    pause
    exit /b 1
)
echo [OK] Harness installed.

:: Step 3: Create local config template
echo.
echo [3/3] Creating harness.local.toml template...
if not exist harness.local.toml (
    (
        echo # Local LLM configuration - secrets and provider settings.
        echo # This file is git-ignored.
        echo.
        echo [llm]
        echo provider = "anthropic"
        echo model = "claude-sonnet-4-6-20250514"
        echo fallback_model = "claude-haiku-3-5-20251001"
        echo api_key = ""
        echo api_base = ""
    ) > harness.local.toml
    echo [OK] Created harness.local.toml
) else (
    echo       harness.local.toml already exists, skipping.
)

echo.
echo ╔══════════════════════════════════════════════╗
echo ║  Setup complete!                            ║
echo ╠══════════════════════════════════════════════╣
echo ║  Next steps:                                ║
echo ║                                             ║
echo ║  1. Edit harness.local.toml                 ║
echo ║     Add your api_key                        ║
echo ║                                             ║
echo ║  2. Run:                                    ║
echo ║     .venv\Scripts\harness.exe               ║
echo ║                                             ║
echo ║  3. Or activate venv first:                 ║
echo ║     .venv\Scripts\activate                   ║
echo ║     harness run "hello world"               ║
echo ╚══════════════════════════════════════════════╝
echo.

pause
