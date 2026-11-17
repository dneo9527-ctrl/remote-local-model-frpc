$ErrorActionPreference = 'Stop'
$version = '0.72.0'
$archiveName = "frp_${version}_windows_amd64.zip"
$url = "https://github.com/fatedier/frp/releases/download/v$version/$archiveName"
# GitHub official release asset digest, frp v0.72.0 windows_amd64.
$expectedHash = '5197c1b69564089cdc229fc1260a0e25db1e722b1b7db45fe1542c3c43edf9ae'
$binaryDirectory = Join-Path $PSScriptRoot 'bin'
$target = Join-Path $binaryDirectory 'frpc.exe'
if (Test-Path -LiteralPath $target) {
    Write-Host 'Keeping existing bin/frpc.exe. Run bin/frpc.exe --version to inspect it.'
    return
}

$temporary = Join-Path ([IO.Path]::GetTempPath()) ('sub2api-frp-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $archivePath = Join-Path $temporary $archiveName
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $archivePath -UseBasicParsing
    if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
        throw 'FRP archive SHA-256 mismatch. Nothing was installed.'
    }
    $extractPath = Join-Path $temporary 'extracted'
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractPath
    $releaseDirectory = Join-Path $extractPath "frp_${version}_windows_amd64"
    New-Item -ItemType Directory -Path $binaryDirectory -Force | Out-Null
    # No overwrite, including if another setup created the file while downloading.
    [IO.File]::Copy((Join-Path $releaseDirectory 'frpc.exe'), $target, $false)
    Copy-Item -LiteralPath (Join-Path $releaseDirectory 'LICENSE') -Destination (Join-Path $binaryDirectory 'frp-LICENSE.txt')
    Write-Host "Installed official frpc $version to bin/frpc.exe (archive SHA-256 verified)."
} finally {
    # All children are from the verified official archive under this unique temp directory.
    $resolvedTemporary = [IO.Path]::GetFullPath($temporary)
    $tempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolvedTemporary.StartsWith($tempParent, [StringComparison]::OrdinalIgnoreCase) -and
        [IO.Path]::GetFileName($resolvedTemporary) -match '^sub2api-frp-[a-f0-9]{32}$') {
        Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force
    }
}
