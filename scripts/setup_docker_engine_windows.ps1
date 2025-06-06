# Tower Hooker - Docker Engine WSL2 Setup Assistant
# This script helps users set up Docker Engine within WSL2 on Windows

Write-Host "========================================" -ForegroundColor Green
Write-Host "Tower Hooker - Docker Engine WSL2 Setup" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# Function to check if running as administrator
function Test-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Function to check WSL status
function Test-WSL {
    try {
        $wslResult = wsl --status 2>$null
        return $true
    }
    catch {
        return $false
    }
}

# Function to list WSL distributions
function Get-WSLDistributions {
    try {
        $result = wsl --list --verbose 2>$null
        return $result
    }
    catch {
        return $null
    }
}

Write-Host "Checking system requirements..." -ForegroundColor Yellow
Write-Host ""

# Check if WSL is available
if (-not (Test-WSL)) {
    Write-Host "❌ WSL is not installed or not available." -ForegroundColor Red
    Write-Host ""
    
    if (Test-Administrator) {
        Write-Host "This script is running as Administrator. Attempting to install WSL..." -ForegroundColor Yellow
        Write-Host ""
        
        try {
            Write-Host "Installing WSL..." -ForegroundColor Yellow
            wsl --install --no-launch
            Write-Host "✅ WSL installation initiated. Please restart your computer and run this script again." -ForegroundColor Green
        }
        catch {
            Write-Host "❌ Failed to install WSL automatically." -ForegroundColor Red
            Write-Host "Please run 'wsl --install' in an Administrator PowerShell manually." -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "Please run the following command in an Administrator PowerShell:" -ForegroundColor Yellow
        Write-Host "wsl --install" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "Then restart your computer and run this script again." -ForegroundColor Yellow
    }
    
    pause
    exit 1
}

Write-Host "✅ WSL is available" -ForegroundColor Green

# List current distributions
Write-Host ""
Write-Host "Current WSL distributions:" -ForegroundColor Yellow
$distributions = Get-WSLDistributions
if ($distributions) {
    Write-Host $distributions
} else {
    Write-Host "No distributions found or unable to list them." -ForegroundColor Red
}

# Check for Ubuntu
$hasUbuntu = $false
if ($distributions -and ($distributions -match "Ubuntu")) {
    $hasUbuntu = $true
    Write-Host "✅ Ubuntu distribution found" -ForegroundColor Green
} else {
    Write-Host "❌ No Ubuntu distribution found" -ForegroundColor Red
    Write-Host ""
    Write-Host "Installing Ubuntu..." -ForegroundColor Yellow
    
    try {
        wsl --install -d Ubuntu --no-launch
        Write-Host "✅ Ubuntu installation initiated" -ForegroundColor Green
        Write-Host "Please wait for the installation to complete and set up your Ubuntu user account." -ForegroundColor Yellow
        $hasUbuntu = $true
    }
    catch {
        Write-Host "❌ Failed to install Ubuntu automatically" -ForegroundColor Red
        Write-Host "Please run 'wsl --install -d Ubuntu' manually" -ForegroundColor Yellow
        pause
        exit 1
    }
}

if ($hasUbuntu) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "Docker Engine Installation Instructions" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    
    Write-Host "Next steps - Please open your Ubuntu WSL2 terminal and run these commands:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "1. Update package lists:" -ForegroundColor Cyan
    Write-Host "   sudo apt update" -ForegroundColor White
    Write-Host ""
    Write-Host "2. Install Docker Engine using the convenience script:" -ForegroundColor Cyan
    Write-Host "   curl -fsSL https://get.docker.com -o get-docker.sh" -ForegroundColor White
    Write-Host "   sudo sh get-docker.sh" -ForegroundColor White
    Write-Host ""
    Write-Host "3. Add your user to the docker group:" -ForegroundColor Cyan
    Write-Host "   sudo usermod -aG docker `$USER" -ForegroundColor White
    Write-Host ""
    Write-Host "4. Start the Docker service:" -ForegroundColor Cyan
    Write-Host "   sudo systemctl start docker" -ForegroundColor White
    Write-Host "   sudo systemctl enable docker" -ForegroundColor White
    Write-Host ""
    Write-Host "5. Close and reopen your WSL2 terminal, or run:" -ForegroundColor Cyan
    Write-Host "   newgrp docker" -ForegroundColor White
    Write-Host ""
    Write-Host "After completing these steps, return here and press any key to test the setup..." -ForegroundColor Yellow
    pause
    
    Write-Host ""
    Write-Host "Testing Docker setup..." -ForegroundColor Yellow
    
    try {
        $dockerTest = docker version 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "✅ Docker is working correctly!" -ForegroundColor Green
            Write-Host ""
            Write-Host "Testing Docker Compose..." -ForegroundColor Yellow
            
            $composeTest = docker compose version 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "✅ Docker Compose is working correctly!" -ForegroundColor Green
                Write-Host ""
                Write-Host "🎉 Setup complete! You can now run Tower Hooker." -ForegroundColor Green
            } else {
                Write-Host "⚠️  Docker Compose may not be available" -ForegroundColor Yellow
                Write-Host "This is usually included with modern Docker Engine installations." -ForegroundColor Yellow
            }
        } else {
            Write-Host "❌ Docker test failed" -ForegroundColor Red
            Write-Host "Please check that you followed all the steps above correctly." -ForegroundColor Yellow
            Write-Host ""
            Write-Host "Common issues:" -ForegroundColor Yellow
            Write-Host "- Docker service not started: sudo systemctl start docker" -ForegroundColor White
            Write-Host "- User not in docker group: sudo usermod -aG docker `$USER" -ForegroundColor White
            Write-Host "- Need to restart WSL2 terminal after adding to group" -ForegroundColor White
        }
    }
    catch {
        Write-Host "❌ Unable to test Docker setup" -ForegroundColor Red
        Write-Host "This might mean Docker is not properly installed or the CLI is not accessible from Windows." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Setup assistant complete. Press any key to exit..." -ForegroundColor Green
pause 