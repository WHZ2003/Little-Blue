# Dot-source before launching/installing DaFeiYu. Never redirect user data or TEMP.
$dafeiyuCacheRoot = Join-Path (Split-Path -Parent $PSScriptRoot) '.cache'
$dafeiyuCacheDirs = @{
    PIP_CACHE_DIR = 'pip'
    UV_CACHE_DIR = 'uv'
    npm_config_cache = 'npm'
    XDG_CACHE_HOME = 'xdg'
    HF_HOME = 'huggingface'
    TORCH_HOME = 'torch'
    NUMBA_CACHE_DIR = 'numba'
}
foreach ($entry in $dafeiyuCacheDirs.GetEnumerator()) {
    $cachePath = Join-Path $dafeiyuCacheRoot $entry.Value
    New-Item -ItemType Directory -Force -Path $cachePath | Out-Null
    [Environment]::SetEnvironmentVariable($entry.Key, $cachePath, 'Process')
}
