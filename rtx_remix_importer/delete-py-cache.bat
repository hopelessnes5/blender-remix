@echo off
setlocal
pushd "%~dp0" || exit /b 1

for %%D in ("operators\__pycache__" "ui\operators\__pycache__" "ui\__pycache__" "__pycache__") do (
    if exist "%%~D\" (
        echo Removing: %%~D
        rmdir /s /q "%%~D"
        if errorlevel 1 echo Failed to remove: %%~D
    )
)

popd
endlocal