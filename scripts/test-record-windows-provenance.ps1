$ErrorActionPreference = 'Stop'
$root = Join-Path ([System.IO.Path]::GetTempPath()) ('pairlet-provenance-' + [guid]::NewGuid())
[void](New-Item -ItemType Directory $root)
$originalSha = $env:GITHUB_SHA
$originalRun = $env:GITHUB_RUN_ID
try {
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $database = $installer.OpenDatabase((Join-Path $root 'fixture.msi'), 3)
    $commands = @(
        'CREATE TABLE `Property` (`Property` CHAR(72) NOT NULL, `Value` CHAR(255) PRIMARY KEY `Property`)',
        "INSERT INTO ``Property`` (``Property``, ``Value``) VALUES ('ProductVersion', '2.2.0')",
        "INSERT INTO ``Property`` (``Property``, ``Value``) VALUES ('ProductName', 'CC Pairlet')",
        "INSERT INTO ``Property`` (``Property``, ``Value``) VALUES ('UpgradeCode', '{230D5F5E-4C7A-3DE9-98EE-6E492CCCB7D0}')"
    )
    foreach ($sql in $commands) {
        $view = $database.OpenView($sql)
        [void]$view.Execute()
        [void]$view.Close()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($view)
    }
    [void]$database.Commit()
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($database)
    $database = $null
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer)
    $installer = $null
    $env:GITHUB_SHA = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    $env:GITHUB_RUN_ID = '123'
    & (Join-Path $PSScriptRoot 'record-windows-provenance.ps1') -Version '2.2.0' -MsiDirectory $root
    $record = Get-Content (Join-Path $root 'provenance.json') -Raw | ConvertFrom-Json
    if ($record.version -ne '2.2.0' -or $record.productName -ne 'CC Pairlet' -or $record.sha256.Length -ne 64) {
        throw 'Provenance regression'
    }
    $rejected = $false
    try { & (Join-Path $PSScriptRoot 'record-windows-provenance.ps1') -Version '9.9.9' -MsiDirectory $root }
    catch { $rejected = $true }
    if (-not $rejected) { throw 'Version mismatch accepted' }
    Write-Host 'MSI provenance fixture and mismatch guard passed'
} finally {
    $env:GITHUB_SHA = $originalSha
    $env:GITHUB_RUN_ID = $originalRun
    if ($null -ne $database) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($database) }
    if ($null -ne $installer) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer) }
    Remove-Item $root -Recurse -Force -ErrorAction Continue
}
