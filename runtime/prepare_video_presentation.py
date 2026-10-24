from pathlib import Path
import argparse, json, hashlib, subprocess, shutil, time
B = Path(__file__).resolve().parent

def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for block in iter(lambda: f.read(2 ** 20), b''):
            h.update(block)
    return h.hexdigest()

def probe(p):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type,codec_name,width,height', '-of', 'json', str(p)], text=True))
p = argparse.ArgumentParser(description='Required cover preparation/check for family-album daily programmes')
p.add_argument('--video', required=True)
p.add_argument('--title', required=True)
p.add_argument('--cover-source', help='Existing 16:9 JPEG actually used in this programme; copied without editing')
p.add_argument('--cover-at', type=float, default=None)
p.add_argument('--expected-video-sha256')
a = p.parse_args()
policy = json.loads((B / 'video-presentation-policy.json').read_text())
v = Path(a.video).resolve()
if not v.is_file():
    raise ValueError('video missing')
digest = sha(v)
if a.expected_video_sha256:
    if not digest == a.expected_video_sha256:
        raise ValueError('video checksum mismatch')
cover = v.with_name(v.stem + '-thumb.jpg')
receipt = v.with_name(v.stem + '.presentation.json')
if cover.exists() or receipt.exists():
    if not (cover.is_file() and receipt.is_file()):
        raise ValueError('partial/existing output requires review; do not overwrite')
    j = json.loads(receipt.read_text())
    if not (j['video_sha256'] == digest and j['cover_sha256'] == sha(cover)):
        raise ValueError('existing presentation pair mismatch')
    if not j['title'] == a.title:
        raise ValueError('Validation failed')
    print(json.dumps(j, ensure_ascii=False))
    raise SystemExit(0)
video_meta = probe(v)
duration = float(video_meta['format']['duration'])
tmp = cover.with_name(cover.stem + '.partial.jpg')
if not not tmp.exists():
    raise ValueError('Validation failed')
if a.cover_source:
    src = Path(a.cover_source).resolve()
    if not (src.is_file() and src.suffix.lower() in ('.jpg', '.jpeg')):
        raise ValueError('Validation failed')
    shutil.copy2(src, tmp)
    origin = {'type': 'existing_programme_photo', 'source_path': str(src), 'source_sha256': sha(src)}
else:
    at = a.cover_at if a.cover_at is not None else min(float(policy['default_video_frame_seconds']), duration / 2)
    if not 0 <= at < duration:
        raise ValueError('Validation failed')
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '2', '-ss', str(at), '-i', str(v), '-frames:v', '1', '-q:v', '2', str(tmp)], check=True)
    origin = {'type': 'actual_programme_frame', 'seconds': at}
m = probe(tmp)
s = next((r for r in m['streams'] if r['codec_type'] == 'video'))
w, h = (s['width'], s['height'])
if not (s['codec_name'] == 'mjpeg' and w >= 1280 and (h >= 720) and (abs(w / h - 16 / 9) < 0.01)):
    raise ValueError('cover must be 16:9 JPEG, minimum1280x720')
subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '1', '-i', str(tmp), '-frames:v', '1', '-f', 'null', '-'], check=True)
pixels = subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-i', str(tmp), '-vf', 'scale=64:36,format=gray', '-frames:v', '1', '-f', 'rawvideo', '-'], timeout=30)
if len(pixels) != 64*36:
    raise ValueError('Incomplete cover brightness sample')
mean = sum(pixels) / len(pixels) / 255
std = (sum((v/255-mean)**2 for v in pixels) / len(pixels))**0.5
if not (mean > 0.025 and std > 0.005):
    raise ValueError('cover looks blank/black; choose a clear frame explicitly')
if not not cover.exists():
    raise ValueError('Validation failed')
tmp.rename(cover)
j = {'status': 'cover_validated', 'created_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'title': a.title, 'video_path': str(v), 'video_sha256': digest, 'cover_path': str(cover), 'cover_sha256': sha(cover), 'cover_dimensions': [w, h], 'cover_bytes': cover.stat().st_size, 'source': origin, 'cover_decode_passed': True, 'luma_mean': mean, 'luma_std': std, 'video_untouched': True, 'policy_version': policy['version'], 'player_binding_tested': False}
receipt.write_text(json.dumps(j, ensure_ascii=False, indent=2))
print(json.dumps(j, ensure_ascii=False))
