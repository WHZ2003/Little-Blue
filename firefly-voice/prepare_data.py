"""Prepare one-speaker 22.05 kHz clips, with source-disjoint holdout data."""
import hashlib
import json
import re
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'data/original/GPT-SoVITs-dataset-Firefly'


def main():
    for part in ['train-v1', 'validation-v1', 'reference']:
        (ROOT / 'data' / part).mkdir(parents=True, exist_ok=True)
    records, rejected = [], []
    sr = 22050
    files = sorted((SOURCE / 'audio').glob('*.wav'))
    curated = {}
    for line in (SOURCE / 'label.list').read_text(encoding='utf-8').splitlines():
        fields = line.split('|', 3)
        if len(fields) == 4:
            curated[fields[0].replace('\\', '/').split('/')[-1]] = fields[3]
    for idx, source in enumerate(files):
        if source.name not in curated:
            rejected.append({'source': source.name, 'reason': 'excluded by author curated list'})
            continue
        if source.stem.endswith('_m') and source.with_name(source.stem[:-2] + '_f.wav').exists():
            rejected.append({'source': source.name, 'reason': 'alternate protagonist duplicate'})
            continue
        text = curated[source.name]
        if len(text.strip('…。，！？、.!? ')) < 4:
            rejected.append({'source': source.name, 'reason': 'short/interjection-only transcript'})
            continue
        audio, _ = librosa.load(source, sr=sr, mono=True)
        if not np.isfinite(audio).all() or np.mean(np.abs(audio) >= .999) > .002:
            rejected.append({'source': source.name, 'reason': 'invalid or clipped audio'})
            continue
        audio, _ = librosa.effects.trim(audio, top_db=38)
        if len(audio) < 2 * sr:
            rejected.append({'source': source.name, 'reason': 'shorter than 2 seconds'})
            continue
        group = re.sub(r'_[fm]$', '', source.stem)
        part = 'validation-v1' if int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 10 == 0 else 'train-v1'
        # Keep the known reference source out of both train and validation.
        if source.stem == 'chapter3_3_firefly_212':
            part = 'reference'
        offset, chunk = 0, 0
        while offset < len(audio):
            end = min(len(audio), offset + 10 * sr)
            if end < len(audio):
                # Cut at the quietest 40 ms region between 7 and 10 seconds.
                search = audio[offset + 7 * sr:end]
                energy = np.convolve(search.astype(np.float64) ** 2, np.ones(882) / 882, mode='valid')[::220]
                end = offset + 7 * sr + int(np.argmin(energy)) * 220 + 441
            segment, _ = librosa.effects.trim(audio[offset:end], top_db=38)
            start = offset
            offset = end
            if len(segment) < 2 * sr or np.sqrt(np.mean(segment ** 2)) < .003:
                continue
            peak = float(np.max(np.abs(segment)))
            rms = float(np.sqrt(np.mean(segment ** 2)))
            gain = min(.08 / max(rms, .0001), .94 / max(peak, .0001), 4)
            dest = ROOT / 'data' / part / f'{source.stem}_{chunk:02d}.wav'
            sf.write(dest, segment * gain, sr, subtype='PCM_16')
            records.append({'file': str(dest.relative_to(ROOT)), 'source': source.name,
                'split': part, 'start_seconds': start / sr, 'seconds': len(segment) / sr})
            chunk += 1
        if idx % 100 == 0:
            print(f'Prepared {idx}/{len(files)} sources', flush=True)
    reference_sources = list((ROOT / 'data/reference-original').rglob('*.wav'))
    if not reference_sources:
        raise RuntimeError('Missing author-provided reference')
    reference, _ = librosa.load(reference_sources[0], sr=sr, mono=True)
    reference, _ = librosa.effects.trim(reference, top_db=38)
    if not 1 <= len(reference) / sr <= 25:
        raise RuntimeError('Invalid reference duration')
    sf.write(ROOT / 'data/reference.wav', reference, sr, subtype='PCM_16')
    summary = {part: {'clips': sum(r['split'] == part for r in records),
                     'minutes': sum(r['seconds'] for r in records if r['split'] == part) / 60}
               for part in ['train-v1', 'validation-v1', 'reference']}
    report = {'source_files': len(files), 'summary': summary, 'reference_seconds': len(reference) / sr,
              'rejected': rejected, 'clips': records,
              'source': 'https://lovefirefly.moe/post_voicegen/'}
    (ROOT / 'data/manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'summary': summary, 'rejected': len(rejected)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
