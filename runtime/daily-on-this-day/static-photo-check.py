"""Validate intended still photos allowing bounded lossy-video quantization."""
from pathlib import Path
import argparse, json, subprocess, hashlib, math, re

def frame(segment, second):
    b = subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-ss', str(second), '-i', str(segment), '-frames:v', '1', '-an', '-vf', 'scale=320:180:flags=area,format=gray', '-f', 'rawvideo', '-'], timeout=45)
    if len(b) != 320 * 180:
        raise ValueError('Incomplete decoded still frame')
    return b

def metrics(a, b):
    if len(a) != len(b) or not a:
        raise ValueError('Frame shape differs')
    diffs = sorted((abs(x - y) for x, y in zip(a, b)))
    mae = sum(diffs) / len(diffs)
    p99 = diffs[math.ceil(0.99 * len(diffs)) - 1]
    return {'gray_mean_absolute_difference': mae, 'gray_p99_absolute_difference': p99, 'gray_max_absolute_difference': diffs[-1], 'passed': mae <= 0.5 and p99 <= 3}

def full_ssim(segment):
    cmd = ['ffmpeg', '-nostdin', '-v', 'info', '-threads', '1', '-ss', '1', '-i', str(segment), '-ss', '5', '-i', str(segment), '-filter_complex_threads', '1', '-filter_complex', '[0:v]trim=end_frame=1,setpts=PTS-STARTPTS[a];[1:v]trim=end_frame=1,setpts=PTS-STARTPTS[b];[a][b]ssim', '-frames:v', '1', '-an', '-f', 'null', '-']
    r = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=45)
    found = re.findall('SSIM.*?All:([0-9.]+)', r.stderr)
    if len(found) != 1:
        raise ValueError('SSIM measurement absent or ambiguous')
    return float(found[0])

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--selection', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--negative-control', action='store_true')
    args = p.parse_args()
    photos = [r for r in json.loads(Path(args.selection).read_text()) if r['kind'] == 'photo']
    if not photos:
        raise ValueError('No still photo selected')
    checks = []
    for r in photos:
        a = frame(r['segment'], 1)
        b = frame(r['segment'], 5)
        m = metrics(a, b)
        m.update(id=r['id'], segment=r['segment'], frame_seconds=[1, 5], decoded_preview_sha256=[hashlib.sha256(x).hexdigest() for x in [a, b]])
        checks.append(m)
    ssim = full_ssim(photos[0]['segment'])
    result = {'method': 'Decoded grayscale spatial difference on every photo plus full-resolution SSIM on representative photo; source uses one fixed image per photo segment', 'thresholds': {'gray_mean_absolute_difference_max': 0.5, 'gray_p99_absolute_difference_max': 3, 'representative_full_resolution_ssim_min': 0.995}, 'photos_checked': len(checks), 'checks': checks, 'representative_full_resolution_ssim': ssim, 'passed': all((x['passed'] for x in checks)) and ssim >= 0.995, 'exact_hash_equality_required': False}
    if args.negative_control:
        a = frame(photos[0]['segment'], 1)
        moved = b''.join((a[y * 320 + 16:(y + 1) * 320] + a[y * 320:y * 320 + 16] for y in range(180)))
        negative = metrics(a, moved)
        result['negative_control'] = {'simulated_horizontal_displacement_pixels_at_preview': 16, 'metrics': negative, 'rejected': not negative['passed']}
        result['passed'] &= not negative['passed']
    out = Path(args.output)
    tmp = out.with_name(out.name + '.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    tmp.replace(out)
    print(json.dumps({'passed': result['passed'], 'photos_checked': len(checks), 'representative_full_resolution_ssim': ssim, 'output': str(out), 'negative_control': result.get('negative_control')}, ensure_ascii=False))
    if not result['passed']:
        raise SystemExit('Still-photo spatial validation failed; no publication')
if __name__ == '__main__':
    main()
