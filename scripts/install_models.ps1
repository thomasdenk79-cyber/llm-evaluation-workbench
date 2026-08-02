# Install required LLM models for Ollama and llama.cpp
# This script can be run in PowerShell to pull models via Ollama and download gguf files for llama.cpp.

# Ollama model pulls (requires Ollama server running locally)
$ollamaModels = @(
    "mixtral:8x22b-q4_K_M",
    "deepseek:flash-q4_K_M",
    "glm:5x-q4_K_M",
    "llama3:8b-q4_K_M",
    "qwen:1.8b-q4_K_M"
)
foreach ($model in $ollamaModels) {
    Write-Host "Pulling Ollama model $model..."
    & ollama pull $model
}

# Llama.cpp model downloads (placeholder URLs – replace with actual URLs when available)
$llamaModelDir = "$env:USERPROFILE\llama.cpp\models"
if (-not (Test-Path -LiteralPath $llamaModelDir)) {
    New-Item -ItemType Directory -Path $llamaModelDir | Out-Null
}
$llamaModels = @(
    @{Name="mixtral-8x22b.gguf"; Url="https://example.com/mixtral-8x22b.gguf"},
    @{Name="deepseek-v4-flash.gguf"; Url="https://example.com/deepseek-v4-flash.gguf"},
    @{Name="glm-5x.gguf"; Url="https://example.com/glm-5x.gguf"},
    @{Name="llama3-8b.gguf"; Url="https://example.com/llama3-8b.gguf"},
    @{Name="qwen-1.8b.gguf"; Url="https://example.com/qwen-1.8b.gguf"}
)
foreach ($m in $llamaModels) {
    $dest = Join-Path $llamaModelDir $m.Name
    if (-not (Test-Path -LiteralPath $dest)) {
        Write-Host "Downloading $($m.Name) to $dest"
        # Use Invoke-WebRequest; replace with actual URL when available
        Invoke-WebRequest -Uri $m.Url -OutFile $dest
    } else {
        Write-Host "Model $($m.Name) already exists, skipping."
    }
}

Write-Host "Model installation script completed."
