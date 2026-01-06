# ============================================
# MoviePilot Plugin Push to GitHub Script
# ============================================

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  MoviePilot Plugin Push Tool" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# 1. Check directory
$expectedPath = "D:\projetcs\rsssubscribe\MoviePilot-Plugins"
if ((Get-Location).Path -ne $expectedPath) {
    Write-Host "Switching to plugin directory..." -ForegroundColor Yellow
    Set-Location $expectedPath
}

# 2. Get GitHub username
Write-Host "Please enter your GitHub username: " -ForegroundColor Green -NoNewline
$githubUsername = Read-Host

if ([string]::IsNullOrWhiteSpace($githubUsername)) {
    Write-Host "Error: Username cannot be empty!" -ForegroundColor Red
    Write-Host "`nPress any key to exit..." -ForegroundColor Yellow
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 1
}

# 3. Check remote repository
Write-Host "`nChecking remote repository configuration..." -ForegroundColor Yellow
$remotes = git remote -v
$hasMyfork = $remotes -match "myfork"

if (-not $hasMyfork) {
    Write-Host "Adding your fork as remote repository..." -ForegroundColor Yellow
    git remote add myfork "https://github.com/$githubUsername/MoviePilot-Plugins.git"
    Write-Host "Remote repository added successfully!" -ForegroundColor Green
} else {
    Write-Host "Remote repository already configured" -ForegroundColor Green
}

# 4. Add files
Write-Host "`nPreparing to commit files..." -ForegroundColor Yellow
git add plugins.v2/DoubanRankPlusOptimized/

# 5. Create commit
Write-Host "Creating commit..." -ForegroundColor Yellow
$commitMsg = @"
feat: Add DoubanRankPlusOptimized plugin

- Improved recognition rate from 63% to 98%
- Removed Douban API dependency, use RSS title directly
- Support URL@@TYPE format to specify media type
- Processing speed increased 4x (3.5min -> 50s)
- Auto convert number suffix to season (Taxi3 -> Taxi Season3)
- Fully customizable RSS configuration
"@

git commit -m $commitMsg

if ($LASTEXITCODE -ne 0) {
    Write-Host "`nCommit failed or no changes to commit" -ForegroundColor Yellow
}

# 6. Push to GitHub
Write-Host "`nPushing to GitHub..." -ForegroundColor Yellow
Write-Host "Target repository: https://github.com/$githubUsername/MoviePilot-Plugins" -ForegroundColor Cyan

git push myfork main

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n========================================" -ForegroundColor Green
    Write-Host "  Push Successful!" -ForegroundColor Green
    Write-Host "========================================`n" -ForegroundColor Green
    
    Write-Host "Next Steps:" -ForegroundColor Cyan
    Write-Host "1. Visit https://github.com/$githubUsername/MoviePilot-Plugins to view your plugin" -ForegroundColor White
    Write-Host "2. Add to MoviePilot config:" -ForegroundColor White
    Write-Host "   PLUGIN_MARKET=https://github.com/$githubUsername/MoviePilot-Plugins" -ForegroundColor Yellow
    Write-Host "3. Restart MoviePilot to see your optimized plugin!" -ForegroundColor White
} else {
    Write-Host "`n========================================" -ForegroundColor Red
    Write-Host "  Push Failed!" -ForegroundColor Red
    Write-Host "========================================`n" -ForegroundColor Red
    
    Write-Host "Possible reasons:" -ForegroundColor Yellow
    Write-Host "1. You haven't forked boeto/MoviePilot-Plugins repository yet" -ForegroundColor White
    Write-Host "   Solution: Visit https://github.com/boeto/MoviePilot-Plugins and click Fork button" -ForegroundColor Cyan
    Write-Host "`n2. First push requires GitHub login" -ForegroundColor White
    Write-Host "   Solution: Login to GitHub in the popup window" -ForegroundColor Cyan
    Write-Host "`n3. Incorrect username" -ForegroundColor White
    Write-Host "   Solution: Visit https://github.com/settings/profile to confirm username and retry" -ForegroundColor Cyan
}

Write-Host "`nPress any key to exit..." -ForegroundColor Yellow
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
