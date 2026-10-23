"""Loopback-only Seed-VC service for Fatfish's existing Edge TTS output.

Keeps model/reference features warm. Text and audio are not logged or retained.
"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT / 'seed-vc')
sys.path.insert(0, str(ROOT / 'seed-vc'))
os.environ['HF_HUB_DISABLE_XET'] = '1'
os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'

import argparse
from contextlib import asynccontextmanager
import io
import json
import threading
import time
from types import SimpleNamespace

import librosa
import numpy as np
import soundfile as sf
import torch
import torchaudio
from fastapi import FastAPI, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
import uvicorn
import inference


class Converter:
    def __init__(self, checkpoint: Path, config: Path, reference: Path, steps=25):
        torch.set_num_threads(4)
        self.steps = steps
        self.checkpoint = checkpoint
        self.lock = threading.Lock()
        self.completed = 0
        self.last_seconds = None
        self.last_error = None
        args = SimpleNamespace(checkpoint=str(checkpoint), config=str(config), f0_condition=False, fp16=True)
        self.model, self.semantic, _, self.vocoder, self.speaker, self.mel_fn, mel_args = inference.load_models(args)
        self.sr = mel_args['sampling_rate']
        self.device = inference.device
        audio, _ = librosa.load(reference, sr=self.sr, mono=True)
        if not 1 <= len(audio) / self.sr <= 15:
            raise ValueError('Reference must be between 1 and 15 seconds')
        self.reference_seconds = len(audio) / self.sr
        with torch.inference_mode():
            waveform = torch.tensor(audio).unsqueeze(0).to(self.device)
            wave16 = torchaudio.functional.resample(waveform, self.sr, 16000)
            semantic = self.semantic(wave16)
            self.prompt_mel = self.mel_fn(waveform)
            feats = torchaudio.compliance.kaldi.fbank(wave16, num_mel_bins=80, dither=0, sample_frequency=16000)
            self.style = self.speaker((feats - feats.mean(0, keepdim=True)).unsqueeze(0))
            length = torch.tensor([self.prompt_mel.size(-1)], device=self.device)
            self.prompt_condition = self.model.length_regulator(semantic, ylens=length, n_quantizers=3, f0=None)[0]

    def convert(self, content: bytes) -> bytes:
        if not self.lock.acquire(blocking=False):
            raise HTTPException(429, 'Voice converter is busy')
        started = time.perf_counter()
        try:
            audio, sr = sf.read(io.BytesIO(content), dtype='float32', always_2d=True)
            audio = audio.mean(axis=1)
            duration = len(audio) / sr
            if not .2 <= duration <= 30 or not np.isfinite(audio).all():
                raise HTTPException(422, 'Expected finite audio between 0.2 and 30 seconds')
            if sr != self.sr:
                audio = librosa.resample(audio, orig_sr=sr, target_sr=self.sr)
            # Short chunks bound VRAM and preserve the 30-second context budget.
            chunks = []
            for offset in range(0, len(audio), self.sr * 12):
                part = audio[offset:offset + self.sr * 12]
                if len(part) < self.sr // 5:
                    part = np.pad(part, (0, self.sr // 5 - len(part)))
                with torch.inference_mode():
                    waveform = torch.tensor(part).unsqueeze(0).to(self.device)
                    wave16 = torchaudio.functional.resample(waveform, self.sr, 16000)
                    semantic = self.semantic(wave16)
                    mel = self.mel_fn(waveform)
                    length = torch.tensor([mel.size(-1)], device=self.device)
                    condition = self.model.length_regulator(semantic, ylens=length, n_quantizers=3, f0=None)[0]
                    combined = torch.cat([self.prompt_condition, condition], dim=1)
                    with torch.autocast(device_type='cuda', dtype=torch.float16):
                        converted = self.model.cfm.inference(combined,
                            torch.tensor([combined.size(1)], device=self.device), self.prompt_mel,
                            self.style, None, self.steps, inference_cfg_rate=.7)
                    converted = converted[:, :, self.prompt_mel.size(-1):]
                    wave = self.vocoder(converted.float()).squeeze().cpu().numpy()
                    chunks.append(wave)
            result = np.concatenate(chunks)
            if not np.isfinite(result).all() or np.sqrt(np.mean(result ** 2)) < 1e-5:
                raise RuntimeError('Invalid/silent synthesis output')
            peak = np.max(np.abs(result))
            if peak > .98:
                result *= .98 / peak
            output = io.BytesIO()
            sf.write(output, result, self.sr, format='WAV', subtype='PCM_16')
            self.completed += 1
            self.last_seconds = round(time.perf_counter() - started, 3)
            self.last_error = None
            print(json.dumps({'event': 'converted', 'input_seconds': round(duration, 3),
                              'elapsed_seconds': self.last_seconds, 'completed': self.completed}), flush=True)
            return output.getvalue()
        except Exception as error:
            self.last_error = type(error).__name__
            raise
        finally:
            self.lock.release()


def create_app(converter):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get('/health')
    def health():
        return {'service': 'dafeiyu-firefly-seed-vc', 'ready': True, 'model': 'firefly-zh-v1',
                'checkpoint': converter.checkpoint.name, 'completed': converter.completed,
                'last_seconds': converter.last_seconds, 'last_error': converter.last_error,
                'device': str(converter.device)}

    @app.post('/convert')
    async def convert(request: Request):
        if request.headers.get('x-fatfish-voice') != '1':
            raise HTTPException(403, 'Missing local client header')
        if request.headers.get('content-type', '').split(';')[0] != 'application/octet-stream':
            raise HTTPException(415, 'Expected audio bytes')
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 4_000_000:
                raise HTTPException(413, 'Audio request too large')
        try:
            wav = await run_in_threadpool(converter.convert, bytes(data))
        except HTTPException:
            raise
        except Exception as error:
            print(json.dumps({'event': 'conversion_failed', 'error': type(error).__name__}), flush=True)
            raise HTTPException(500, 'Voice conversion failed; use original TTS') from error
        return Response(wav, media_type='audio/wav')

    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18892)
    parser.add_argument('--checkpoint', type=Path, default=ROOT / 'models/firefly-zh-v1/ft_model.pth')
    parser.add_argument('--config', type=Path, default=ROOT / 'models/firefly-zh-v1/config.yml')
    parser.add_argument('--reference', type=Path, default=ROOT / 'data/reference.wav')
    parser.add_argument('--steps', type=int, default=25)
    args = parser.parse_args()
    engine = Converter(args.checkpoint, args.config, args.reference, args.steps)
    uvicorn.run(create_app(engine), host='127.0.0.1', port=args.port, access_log=False)
