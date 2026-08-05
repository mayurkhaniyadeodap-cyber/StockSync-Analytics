<#
.SYNOPSIS
  StockSync Analytics task runner.

.DESCRIPTION
  One entry point for every routine command, so "run the checks" means the same
  thing locally and in CI.

  Frontend commands go through npm scripts, so `./tasks.ps1 check` and running
  `npm run check` by hand do exactly the same thing.

.EXAMPLE
  ./tasks.ps1 setup      # install backend + frontend dependencies
  ./tasks.ps1 check      # lint, format, typecheck and test both sides
  ./tasks.ps1 dev        # run API and web dev server together
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'check', 'lint', 'format', 'typecheck', 'test', 'dev',
                 'api', 'web', 'build', 'migrate', 'revision', 'seed', 'reset-db', 'help')]
    [string]$Task = 'help',

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$py = Join-Path $backend '.venv\Scripts\python.exe'

function Write-Step($message) {
    Write-Host ""
    Write-Host "── $message " -ForegroundColor Cyan -NoNewline
    Write-Host ('─' * [Math]::Max(0, 68 - $message.Length)) -ForegroundColor DarkGray
}

function Assert-LastExitCode($what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

function Invoke-Backend([string[]]$Arguments) {
    if (-not (Test-Path $py)) { throw "No virtualenv. Run: ./tasks.ps1 setup" }
    Push-Location $backend
    try { & $py @Arguments; Assert-LastExitCode ($Arguments -join ' ') }
    finally { Pop-Location }
}

function Invoke-Npm([string[]]$Arguments) {
    if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
        throw "No node_modules. Run: ./tasks.ps1 setup"
    }
    Push-Location $frontend
    try { & npm @Arguments; Assert-LastExitCode ("npm " + ($Arguments -join ' ')) }
    finally { Pop-Location }
}

switch ($Task) {
    'setup' {
        Write-Step 'backend: virtualenv + dependencies'
        if (-not (Test-Path $py)) {
            Push-Location $backend
            try { & python -m venv .venv; Assert-LastExitCode 'python -m venv' }
            finally { Pop-Location }
        }
        Invoke-Backend @('-m', 'pip', 'install', '--quiet', '--upgrade', 'pip')
        Invoke-Backend @('-m', 'pip', 'install', '--quiet', '-e', '.[dev]')

        Write-Step 'frontend: npm install'
        Push-Location $frontend
        try { & npm install --no-audit --no-fund; Assert-LastExitCode 'npm install' }
        finally { Pop-Location }

        if (-not (Test-Path (Join-Path $root '.env'))) {
            Copy-Item (Join-Path $root '.env.example') (Join-Path $root '.env')
            Write-Host "`nCreated .env from .env.example — fill in the password before running." -ForegroundColor Yellow
        }
        Write-Host "`nSetup complete." -ForegroundColor Green
    }

    'lint' {
        Write-Step 'backend: ruff'
        Invoke-Backend @('-m', 'ruff', 'check', '.')
        Write-Step 'frontend: eslint'
        Invoke-Npm @('run', 'lint')
    }

    'format' {
        Write-Step 'backend: ruff format'
        Invoke-Backend @('-m', 'ruff', 'format', '.')
        Write-Step 'frontend: prettier'
        Invoke-Npm @('run', 'format')
    }

    'typecheck' {
        Write-Step 'backend: mypy'
        Invoke-Backend @('-m', 'mypy', 'app')
        Write-Step 'frontend: tsc'
        Invoke-Npm @('run', 'typecheck')
    }

    'test' {
        Write-Step 'backend: pytest'
        Invoke-Backend @('-m', 'pytest')
        Write-Step 'frontend: vitest'
        Invoke-Npm @('run', 'test')
    }

    'check' {
        Write-Step 'backend: ruff check'
        Invoke-Backend @('-m', 'ruff', 'check', '.')
        Write-Step 'backend: ruff format --check'
        Invoke-Backend @('-m', 'ruff', 'format', '--check', '.')
        Write-Step 'backend: mypy'
        Invoke-Backend @('-m', 'mypy', 'app')
        Write-Step 'backend: pytest'
        Invoke-Backend @('-m', 'pytest')

        Write-Step 'frontend: eslint'
        Invoke-Npm @('run', 'lint')
        Write-Step 'frontend: prettier --check'
        Invoke-Npm @('run', 'format:check')
        Write-Step 'frontend: tsc'
        Invoke-Npm @('run', 'typecheck')
        Write-Step 'frontend: vitest'
        Invoke-Npm @('run', 'test')

        Write-Host "`nAll checks passed." -ForegroundColor Green
    }

    'api' {
        Invoke-Backend @('-m', 'uvicorn', 'app.main:app', '--reload', '--port', '8000')
    }

    'web' { Invoke-Npm @('run', 'dev') }

    'dev' {
        Write-Host "API  → http://127.0.0.1:8000/api/docs" -ForegroundColor Cyan
        Write-Host "Web  → http://localhost:5173" -ForegroundColor Cyan
        Write-Host "Ctrl+C stops the web server; the API window closes with it.`n"
        $apiJob = Start-Process -PassThru -NoNewWindow -FilePath $py `
            -ArgumentList '-m', 'uvicorn', 'app.main:app', '--reload', '--port', '8000' `
            -WorkingDirectory $backend
        try { Invoke-Npm @('run', 'dev') }
        finally { if (-not $apiJob.HasExited) { Stop-Process -Id $apiJob.Id -Force } }
    }

    'build' {
        Write-Step 'frontend: production build'
        Invoke-Npm @('run', 'build')
    }

    'migrate' { Invoke-Backend (@('-m', 'alembic', 'upgrade') + $(if ($Rest) { $Rest } else { @('head') })) }

    'revision' {
        if (-not $Rest) { throw 'Usage: ./tasks.ps1 revision "add workspaces"' }
        Invoke-Backend @('-m', 'alembic', 'revision', '--autogenerate', '-m', ($Rest -join ' '))
    }

    'seed' { Invoke-Backend (@('-m', 'app.cli', 'seed') + $Rest) }

    'reset-db' {
        # SQLite is a file: deleting it is the whole reset. Migrations rebuild it.
        $dbFiles = Get-ChildItem (Join-Path $root 'data') -Filter 'stocksync.db*' -ErrorAction SilentlyContinue
        if ($dbFiles) {
            $dbFiles | Remove-Item -Force
            Write-Host "Deleted $($dbFiles.Count) database file(s)." -ForegroundColor Yellow
        } else {
            Write-Host 'No database file to delete.' -ForegroundColor Gray
        }
        Write-Step 'alembic upgrade head'
        Invoke-Backend @('-m', 'alembic', 'upgrade', 'head')

        # A schema with no account in it cannot be signed into, so the rebuild
        # is not finished until the default administrator exists.
        Write-Step 'seed: default administrator'
        Invoke-Backend @('-m', 'app.cli', 'seed')
    }

    default {
        # A plain array rather than a here-string: here-string terminators are
        # sensitive to line endings, and this file round-trips through git with
        # eol normalisation.
        $help = @(
            ''
            'StockSync Analytics tasks'
            ''
            '  setup       Install backend venv + npm dependencies, seed .env'
            '  check       Lint, format-check, typecheck and test BOTH sides'
            '              (run this before every commit)'
            '  lint        Lint only            format      Rewrite formatting'
            '  typecheck   Types only           test        Tests only'
            '  dev         Run API + web together'
            '  api         API only (:8000)     web         Web only (:5173)'
            '  build       Production frontend build'
            '  migrate     alembic upgrade head'
            '  revision    alembic revision --autogenerate -m "<message>"'
            '  seed        Create the default administrator (admin@deodap.in)'
            '  reset-db    Delete the SQLite file, rebuild it from migrations, seed'
            ''
        )
        Write-Host ($help -join [Environment]::NewLine) -ForegroundColor Gray
    }
}
