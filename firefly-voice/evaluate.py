"""Compare pretrained/fine-tuned samples and check their intelligibility with local ASR."""
from pathlib import Path
import json
import io
import re
import time

from service import Converter, ROOT
import librosa
import numpy as np
import soundfile as sf
import torch
import torchaudio
from opencc import OpenCC
from modules.commons import load_checkpoint


def edit_distance(a, b):
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        new = [i]
        for j, y in enumerate(b, 1):
            new.append(min(new[-1] + 1, row[j] + 1, row[j - 1] + (x != y)))
        row = new
    return row[-1]


def normalized(text):
    return re.sub(r'[^\u4e00-\u9fffa-z0-9]', '', OpenCC('t2s').convert(text).lower())


def main():
    model_dir = ROOT / 'models/firefly-zh-v1'
    engine = Converter(model_dir / 'ft_model.pth', model_dir / 'config.yml', ROOT / 'data/reference.wav')
    sources = sorted((ROOT / 'samples').glob('source-*.mp3'))
    expected = json.loads((ROOT / 'samples/texts.json').read_text(encoding='utf-8'))
    report = {'samples': [], 'reference_seconds': engine.reference_seconds}
    for mode in ['trained', 'pretrained']:
        if mode == 'pretrained':
            ckpt = next((ROOT / 'seed-vc/checkpoints/models--Plachta--Seed-VC/snapshots').glob('*/DiT_seed_v2_uvit_whisper_small_wavenet_bigvgan_pruned.pth'))
            load_checkpoint(engine.model, None, str(ckpt), load_only_params=True)
        for i, source in enumerate(sources):
            torch.manual_seed(2026 + i)
            wav = engine.convert(source.read_bytes())
            output = ROOT / 'samples' / f'{mode}-{i + 1}.wav'
            output.write_bytes(wav)
            audio, sr = sf.read(io.BytesIO(wav), dtype='float32')
            with torch.inference_mode():
                wave16 = torchaudio.functional.resample(torch.tensor(audio).unsqueeze(0).to(engine.device), sr, 16000)
                feats = torchaudio.compliance.kaldi.fbank(wave16, num_mel_bins=80, dither=0, sample_frequency=16000)
                embedding = engine.speaker((feats - feats.mean(0, keepdim=True)).unsqueeze(0))
                similarity = torch.nn.functional.cosine_similarity(embedding, engine.style).item()
            report['samples'].append({'mode': mode, 'file': str(output.relative_to(ROOT)), 'expected': expected[i],
                'duration': len(audio) / sr, 'peak': float(np.max(np.abs(audio))),
                'rms': float(np.sqrt(np.mean(audio ** 2))), 'speaker_cosine': similarity,
                'conversion_seconds': engine.last_seconds})
    # Use a local Chinese-capable recognizer; this is an automatic check, not human listening.
    del engine
    torch.cuda.empty_cache()
    from transformers import WhisperForConditionalGeneration, WhisperFeatureExtractor, WhisperTokenizer
    name = 'openai/whisper-small'
    model = WhisperForConditionalGeneration.from_pretrained(name, local_files_only=True, torch_dtype=torch.float16).cuda().eval()
    extractor = WhisperFeatureExtractor.from_pretrained(name, local_files_only=True)
    tokenizer = WhisperTokenizer.from_pretrained(str(ROOT / 'models/whisper-tokenizer'), language='zh', task='transcribe')
    decoder_ids = tokenizer.get_decoder_prompt_ids(language='zh', task='transcribe', no_timestamps=True)
    for sample in report['samples']:
        audio, _ = librosa.load(ROOT / sample['file'], sr=16000)
        features = extractor(audio, sampling_rate=16000, return_tensors='pt').input_features.cuda().half()
        with torch.inference_mode():
            tokens = model.generate(features, forced_decoder_ids=decoder_ids, max_new_tokens=100)
        actual = tokenizer.batch_decode(tokens, skip_special_tokens=True)[0]
        expected_text, actual_text = normalized(sample['expected']), normalized(actual)
        sample['transcript'] = actual
        sample['character_error_rate'] = edit_distance(expected_text, actual_text) / max(1, len(expected_text))
    (ROOT / 'samples/evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
