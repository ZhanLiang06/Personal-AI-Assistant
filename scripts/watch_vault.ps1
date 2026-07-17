$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env"
$SyncScript = Join-Path $PSScriptRoot "sync_vault.cmd"
$TodoReconcileScript = Join-Path `
    $PSScriptRoot `
    "reconcile_todo_reminders.cmd"

$QuietPeriod = [TimeSpan]::FromMinutes(5)
$TodoQuietPeriod = [TimeSpan]::FromMinutes(1)


$vaultSetting = Get-Content -LiteralPath $EnvFile |
    Where-Object { $_ -match '^\s*VAULT_PATH\s*=' } |
    Select-Object -First 1

$VaultPath = ($vaultSetting -split "=", 2)[1].Trim().Trim('"').Trim("'")

$state = [hashtable]::Synchronized(@{
    VaultPending       = $false
    VaultLastChange    = [DateTime]::MinValue

    TodoPending        = $false
    TodoLastChange     = [DateTime]::MinValue
})

$watcher = [System.IO.FileSystemWatcher]::new()

$watcher.Path = $VaultPath
$watcher.IncludeSubdirectories = $true
$watcher.Filter = "*.*"

$watcher.NotifyFilter = (
    [System.IO.NotifyFilters]::FileName -bor
    [System.IO.NotifyFilters]::DirectoryName -bor
    [System.IO.NotifyFilters]::LastWrite -bor
    [System.IO.NotifyFilters]::Size
)


$eventContext = @{
    State     = $state
    VaultPath = $VaultPath
}



$onVaultChange = {
    $changedPath = $Event.SourceEventArgs.FullPath
    $context = $Event.MessageData
    $vaultRoot = $context.VaultPath
    $watchState = $context.State

    $obsidianPath = Join-Path $vaultRoot ".obsidian"

    $isObsidianPath = (
        $changedPath.Equals(
            $obsidianPath,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        $changedPath.StartsWith(
            $obsidianPath + "\",
            [System.StringComparison]::OrdinalIgnoreCase
        )
    )

    if ($isObsidianPath) {
        return
    }

    $extension = [System.IO.Path]::GetExtension(
        $changedPath
    ).ToLowerInvariant()

    $relevantExtensions = @(
        ".md",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp"
    )

    if ($extension -notin $relevantExtensions) {
        return
    }

    $now = [DateTime]::Now

    $watchState.VaultLastChange = $now
    $watchState.VaultPending = $true

    $todoFolder = Join-Path $vaultRoot "Journal\to-dos"
    $todoFolderPrefix = (
        $todoFolder +
        [System.IO.Path]::DirectorySeparatorChar
    )

    $isTodoFile = (
        $extension -eq ".md" -and
        $changedPath.StartsWith(
            $todoFolderPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    )

    if ($isTodoFile) {
        $watchState.TodoLastChange = $now
        $watchState.TodoPending = $true
    }
}


$subscriptions = @(
    Register-ObjectEvent `
        -InputObject $watcher `
        -EventName Changed `
        -Action $onVaultChange `
        -MessageData $eventContext

    Register-ObjectEvent `
        -InputObject $watcher `
        -EventName Created `
        -Action $onVaultChange `
        -MessageData $eventContext

    Register-ObjectEvent `
        -InputObject $watcher `
        -EventName Deleted `
        -Action $onVaultChange `
        -MessageData $eventContext

    Register-ObjectEvent `
        -InputObject $watcher `
        -EventName Renamed `
        -Action $onVaultChange `
        -MessageData $eventContext
)


$watcher.EnableRaisingEvents = $true


try {
    while ($true) {
        Start-Sleep -Seconds 5

        if ($state.TodoPending) {
            $timeSinceTodoChange = (
                [DateTime]::Now -
                $state.TodoLastChange
            )

            if (
                $timeSinceTodoChange -ge
                $TodoQuietPeriod
            ) {
                $state.TodoPending = $false

                if (
                    Test-Path `
                        -LiteralPath $TodoReconcileScript
                ) {
                    & $TodoReconcileScript
                }
            }
        }

        if ($state.VaultPending) {
            $timeSinceVaultChange = (
                [DateTime]::Now -
                $state.VaultLastChange
            )

            if (
                $timeSinceVaultChange -ge
                $QuietPeriod
            ) {
                $state.VaultPending = $false

                & $SyncScript
            }
        }
    }
}
finally {
    $watcher.EnableRaisingEvents = $false

    foreach ($subscription in $subscriptions) {
        Unregister-Event `
            -SubscriptionId $subscription.Id
    }

    $watcher.Dispose()
}
