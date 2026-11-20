@echo off
rem Para a telinha customizada (qualquer python rodando screen.py)
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*screen.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
