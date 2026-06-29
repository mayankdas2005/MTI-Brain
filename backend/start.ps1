$PORT = 8001
$pids = @(netstat -ano | Select-String ":$PORT\s" | ForEach-Object {
    ($_ -split '\s+') | Select-Object -Last 1
} | Where-Object { $_ -match '^\d+$' -and $_ -ne '0' } | Sort-Object -Unique)

foreach ($p in $pids) {
    try { Stop-Process -Id ([int]$p) -Force -ErrorAction SilentlyContinue } catch {}
}

if ($pids.Count -gt 0) {
    Write-Host "Cleared $($pids.Count) process(es) on port $PORT"
}

uvicorn app.main:app --host 0.0.0.0 --port $PORT --reload
