$ErrorActionPreference = 'Stop'
$appDirectory = Join-Path $PSScriptRoot 'backend'
$pythonExecutable = Join-Path $appDirectory '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw '请先按backend/backend/README.md建立项目虚拟环境。'
}
Set-Location -LiteralPath $appDirectory
& $pythonExecutable -m uvicorn main:app --host 127.0.0.1 --port 8000
exit $LASTEXITCODE
