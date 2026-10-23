import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parent
os.environ['HF_HUB_DISABLE_XET'] = '1'
os.environ['HF_HUB_CACHE'] = str(ROOT / 'seed-vc/checkpoints/hf_cache')
os.environ['HF_HUB_DOWNLOAD_TIMEOUT'] = '120'
from huggingface_hub import hf_hub_download, snapshot_download


def source_models():
    for repo, names in [
        ('Plachta/Seed-VC', ['DiT_seed_v2_uvit_whisper_small_wavenet_bigvgan_pruned.pth', 'config_dit_mel_seed_uvit_whisper_small_wavenet.yml', 'se_db.pt']),
        ('funasr/campplus', ['campplus_cn_common.bin']),
        ('myshell-ai/OpenVoiceV2', ['converter/checkpoint.pth', 'converter/config.json']),
    ]:
        for name in names:
            print('Downloading', repo, name, flush=True)
            hf_hub_download(repo, name, cache_dir=str(ROOT / 'seed-vc/checkpoints'))


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(source_models),
            executor.submit(snapshot_download, 'openai/whisper-small', allow_patterns=['config.json', 'preprocessor_config.json', 'model.safetensors']),
            executor.submit(snapshot_download, 'nvidia/bigvgan_v2_22khz_80band_256x', allow_patterns=['config.json', 'bigvgan_generator.pt'])]
        for future in futures:
            future.result()
    print('Required model downloads complete', flush=True)
