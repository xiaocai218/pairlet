param([Parameter(Mandatory=$true)][string]$Version, [string]$MsiDirectory = 'out')
$ErrorActionPreference = 'Stop'
$msi = @(Get-ChildItem $MsiDirectory -Filter '*.msi')
if ($msi.Count -ne 1) { throw 'Exactly one MSI is required' }
$installer = New-Object -ComObject WindowsInstaller.Installer
$database = $installer.OpenDatabase($msi[0].FullName, 0)
function Read-MsiProperty([string]$Name) {
    $view = $database.OpenView("SELECT ``Value`` FROM ``Property`` WHERE ``Property`` = '$Name'")
    $record = $null
    try {
        [void]$view.Execute()
        $record = $view.Fetch()
        if ($null -eq $record) { throw "Missing MSI property: $Name" }
        return $record.GetType().InvokeMember('StringData', 'GetProperty', $null, $record, 1)
    } finally {
        if ($null -ne $record) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($record) }
        [void]$view.Close()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($view)
    }
}
try {
$actual = Read-MsiProperty 'ProductVersion'
if ($actual -ne $Version) { throw "MSI version mismatch: $actual != $Version" }
$contract = Get-Content (Join-Path $PSScriptRoot '../packaging/brand-compatibility.json') -Raw | ConvertFrom-Json
$upgradeCode = Read-MsiProperty 'UpgradeCode'
if ($upgradeCode.Trim('{}') -ine $contract.windowsUpgradeCode.Trim('{}')) { throw 'MSI UpgradeCode mismatch' }
$result = @{
    schema = 1
    sourceCommit = $env:GITHUB_SHA
    runId = $env:GITHUB_RUN_ID
    version = $actual
    productName = Read-MsiProperty 'ProductName'
    upgradeCode = $upgradeCode
    file = $msi[0].Name
    size = $msi[0].Length
    sha256 = (Get-FileHash $msi[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$result | ConvertTo-Json | Set-Content (Join-Path $MsiDirectory 'provenance.json') -Encoding utf8
} finally {
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($database)
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer)
}
