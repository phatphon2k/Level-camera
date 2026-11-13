# Run from an elevated PowerShell window. Configures the existing IIS site on 4443.
# The rewrite file is backed up before it is changed; inbound access is limited to the LAN.
#Requires -RunAsAdministrator

$ErrorActionPreference = 'Stop'
$appcmd = Join-Path $env:windir 'System32\inetsrv\appcmd.exe'
if (-not (Test-Path -LiteralPath $appcmd)) {
    throw 'IIS appcmd.exe was not found.'
}

$siteLines = & $appcmd list site
if ($LASTEXITCODE -ne 0) { throw 'Could not read IIS sites.' }
$siteLine = $siteLines | Where-Object { $_ -match ':4443:' } | Select-Object -First 1
if (-not $siteLine -or $siteLine -notmatch '^SITE "([^"]+)"') {
    throw 'No IIS site is currently bound to HTTPS port 4443.'
}
$siteName = $Matches[1]
$vdir = & $appcmd list vdir "$siteName/" /text:physicalPath
if ($LASTEXITCODE -ne 0 -or -not $vdir) { throw "Could not find the root directory for IIS site $siteName." }
$siteRoot = [Environment]::ExpandEnvironmentVariables($vdir.Trim())
$webConfig = Join-Path $siteRoot 'web.config'
if (-not (Test-Path -LiteralPath $webConfig)) { throw "IIS site's web.config was not found: $webConfig" }

# Preserve the existing IIS rules and update only its reverse-proxy target.
$backup = "$webConfig.waterai-backup"
if (-not (Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $webConfig -Destination $backup }
[xml]$xml = [System.IO.File]::ReadAllText($webConfig)
$rule = $xml.SelectSingleNode('/configuration/system.webServer/rewrite/rules/rule[@name="ReverseProxyInboundRule1"]')
if (-not $rule) {
    $systemWebServer = $xml.SelectSingleNode('/configuration/system.webServer')
    if (-not $systemWebServer) { throw 'web.config has no system.webServer section; refusing to overwrite it.' }
    $rewrite = $xml.CreateElement('rewrite')
    $rules = $xml.CreateElement('rules')
    $rule = $xml.CreateElement('rule')
    $rule.SetAttribute('name', 'ReverseProxyInboundRule1')
    $rule.SetAttribute('stopProcessing', 'true')
    $match = $xml.CreateElement('match')
    $match.SetAttribute('url', '(.*)')
    $action = $xml.CreateElement('action')
    $action.SetAttribute('type', 'Rewrite')
    $rule.AppendChild($match) | Out-Null
    $rule.AppendChild($action) | Out-Null
    $rules.AppendChild($rule) | Out-Null
    $rewrite.AppendChild($rules) | Out-Null
    $systemWebServer.AppendChild($rewrite) | Out-Null
} else {
    $action = $rule.SelectSingleNode('action')
    if (-not $action) { throw 'Existing reverse-proxy rule has no action; refusing to overwrite it.' }
}
$action.SetAttribute('type', 'Rewrite')
$action.SetAttribute('url', 'http://127.0.0.1:5530/{R:1}')
$xml.Save($webConfig)

# ARR must be enabled at the IIS server level in addition to the site rewrite rule.
& $appcmd set config -section:system.webServer/proxy /enabled:true /commit:apphost
if ($LASTEXITCODE -ne 0) { throw 'Could not enable ARR proxy mode.' }

# Allow HTTPS requests from local subnets only; keep the backend bound to localhost.
$firewallName = 'WaterAI IIS HTTPS (Local Subnet)'
if (-not (Get-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $firewallName -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort 4443 -RemoteAddress LocalSubnet -Profile Domain,Private,Public | Out-Null
}

Write-Output "IIS reverse proxy is configured for site '$siteName'."
Write-Output 'WaterAI backend: http://127.0.0.1:5530'
Write-Output 'LAN URL: https://192.168.110.164:4443/?cam=CAM01'
