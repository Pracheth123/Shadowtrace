# Build a pinned release tarball on Windows (PowerShell 5.1+, Git, Node 18+).
#   powershell -ExecutionPolicy Bypass -File deploy\scripts\build_release.ps1
# Output: dist-release\shadowtrace-<sha>.tar.gz (+ .sha256). Refuses a dirty tree.
$ErrorActionPreference = "Stop"
Set-Location (git rev-parse --show-toplevel)
if ((git status --porcelain) -and ($env:ALLOW_DIRTY -ne "1")) {
  throw "Refusing: working tree has uncommitted changes (set ALLOW_DIRTY=1 for a test build)."
}
$sha = (git rev-parse --short=12 HEAD).Trim()
$name = "shadowtrace-$sha"
$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("st-release-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force (Join-Path $stage $name) | Out-Null
try {
  Push-Location client; npm ci; if ($LASTEXITCODE) { throw "npm ci failed" }; npm run build; if ($LASTEXITCODE) { throw "build failed" }; Pop-Location
  $tarFile = Join-Path $stage "src.tar"
  git archive --format=tar -o $tarFile HEAD src pyproject.toml requirements-lock.txt requirements-postgres.txt README.md config deploy tools/ops docs/DEPLOYMENT.md
  tar -xf $tarFile -C (Join-Path $stage $name); Remove-Item $tarFile
  New-Item -ItemType Directory -Force (Join-Path $stage "$name\client") | Out-Null
  Copy-Item -Recurse client\dist (Join-Path $stage "$name\client\dist")
  Set-Content -Encoding ascii -NoNewline (Join-Path $stage "$name\RELEASE") $sha
  New-Item -ItemType Directory -Force dist-release | Out-Null
  $archive = "dist-release\$name.tar.gz"
  tar -czf $archive -C $stage $name
  $hash = (Get-FileHash -Algorithm SHA256 $archive).Hash.ToLower()
  Set-Content -Encoding ascii "$archive.sha256" "$hash  $name.tar.gz"
  Write-Output "built $archive"
} finally {
  Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
}
