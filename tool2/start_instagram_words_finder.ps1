$ProjectDir = "C:\Users\HP\Downloads\recherche par mots in instagram"
$Python = "C:\Users\HP\AppData\Local\Programs\Python\Python313\python.exe"
$Port = 5000
$OutLog = Join-Path $ProjectDir "server.out.log"
$ErrLog = Join-Path $ProjectDir "server.err.log"

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    exit 0
}

Start-Process -FilePath $Python -ArgumentList "app.py" -WorkingDirectory $ProjectDir -WindowStyle Hidden -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
