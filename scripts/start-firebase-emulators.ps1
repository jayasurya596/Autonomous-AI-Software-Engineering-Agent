$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$localJdk = Join-Path $projectRoot ".tools\jdk\jdk-21.0.13+9"
$javaHome = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { $localJdk }
$javaExe = Join-Path $javaHome "bin\java.exe"

if (-not (Test-Path -LiteralPath $javaExe)) {
    throw "Java 21 is required for the Firebase emulators. Set JAVA_HOME or install a local JDK."
}

$env:JAVA_HOME = $javaHome
$env:PATH = "$(Join-Path $javaHome 'bin');$env:PATH"
$javaSocketTemp = Join-Path $env:SystemRoot "Temp"
$unixSocketOption = "-Djdk.net.unixdomain.tmpdir=$javaSocketTemp"
if ($env:JAVA_TOOL_OPTIONS -notmatch "-Djdk\.net\.unixdomain\.tmpdir=") {
    $env:JAVA_TOOL_OPTIONS = "$(($env:JAVA_TOOL_OPTIONS + ' ' + $unixSocketOption).Trim())"
}
Push-Location $projectRoot
try {
    npx --yes --package=firebase-tools@15.35.0 --package=rc firebase emulators:start --only auth,firestore --project demo-software-agent
}
finally {
    Pop-Location
}
