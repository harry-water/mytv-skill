from pathlib import Path
import json, subprocess, time, hashlib, traceback, argparse, sys
import os
B = Path(os.environ['MYTV_RUNTIME_ROOT'])
W = Path(__file__).resolve().parent
CTX = json.loads((W / 'context.json').read_text())
sys.path.insert(0, str(B / 'daily-on-this-day'))
from duration_plan import plan, check_actual
from video_clip import clip_spec, atempo
from encoding import command_for_backend
DURATION_POLICY = CTX['duration_policy']
STYLE = CTX['presentation']
FONT = STYLE['font_bold']
SIZES = STYLE['font_sizes']
if not hashlib.sha256(Path(FONT).read_bytes()).hexdigest() == STYLE['font_sha256']:
    raise ValueError('bold font asset changed')
THEME = STYLE['themes_by_month'].get(str(CTX['month']), {})
for asset in THEME.values():
    if not hashlib.sha256(Path(asset['path']).read_bytes()).hexdigest() == asset['sha256']:
        raise ValueError('theme asset changed')
for n in ['assets', 'segments', 'pieces', 'logs']:
    (W / n).mkdir(exist_ok=True)
X = 0.7
FPS = 30
ENC = ['-c:v', 'h264_nvenc', '-preset', 'p4', '-rc', 'vbr', '-cq', '18', '-b:v', '0', '-profile:v', 'high', '-level:v', '4.1', '-pix_fmt', 'yuv420p', '-g', '30', '-bf', '0']
FF = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '2', '-filter_complex_threads', '1', '-filter_threads', '1']

def run(a, n):
    a = command_for_backend(a, CTX.get('encoding_backend', 'nvenc'))
    with (W / 'logs' / f'{n}.log').open('w') as f:
        r = subprocess.run([str(x) for x in a], stdout=f, stderr=subprocess.STDOUT)
    if r.returncode:
        raise RuntimeError(n + ': ' + (W / 'logs' / f'{n}.log').read_text()[-2500:])

def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for x in iter(lambda: f.read(2 ** 20), b''):
            h.update(x)
    return h.hexdigest()

def probe(p):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_name,codec_type,width,height,pix_fmt,color_range,color_space,color_primaries,color_transfer', '-of', 'json', str(p)], text=True))

def save(**d):
    d['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    (W / 'render-progress.json').write_text(json.dumps(d, ensure_ascii=False, indent=2))
    print(json.dumps(d, ensure_ascii=False), flush=True)

def photo(r):
    p = Path(r['source_path'])
    key = r['input_sha256'][:16]
    out = W / 'assets' / f'{key}.jpg'
    if not out.exists():
        caption = r['caption']
        run(['magick', str(p) + '[0]', '-auto-orient', '-colorspace', 'sRGB', '-write', 'mpr:src', '+delete', '(', 'mpr:src', '-resize', '1920x1080^', '-gravity', 'center', '-extent', '1920x1080', '-blur', '0x28', '-brightness-contrast', '-18x-8', ')', '(', 'mpr:src', '-resize', '1780x930', ')', '-gravity', 'center', '-geometry', '+0-22', '-compose', 'over', '-composite', '-fill', '#111923B0', '-draw', 'rectangle 0,996 1920,1080', '-font', FONT, '-pointsize', str(SIZES['caption']), '-fill', '#F4F0E8', '-gravity', 'southwest', '-annotate', '+62+20', caption, '-gravity', 'southeast', '-fill', '#D3BB91', '-pointsize', str(SIZES['category']), '-annotate', '+62+22', r['category'], '-quality', '93', out], f'canvas-{key}')
        size = subprocess.check_output(['magick', 'identify', '-format', '%w %h', str(out)], text=True)
        if not size == '1920 1080':
            raise ValueError(size)
    return out

def segment(r):
    key = r['input_sha256'][:16]
    if r['kind'] == 'video':
        excerpt = clip_spec(r, r['duration'])
        r['video_excerpt'] = excerpt
        key += '-' + hashlib.sha256(json.dumps(excerpt, sort_keys=True).encode()).hexdigest()[:8]
    out = W / 'segments' / f'{key}.mkv'
    r['segment'] = str(out)
    if out.exists():
        if not abs(float(probe(out)['format']['duration']) - r['duration']) < 0.1:
            raise ValueError('Validation failed')
        return
    p = Path(r['source_path'])
    st = p.stat()
    if not [st.st_size, st.st_mtime_ns] == r['original_stat']:
        raise ValueError('Validation failed')
    if not sha(p) == r['input_sha256']:
        raise ValueError('Validation failed')
    if r['kind'] == 'photo':
        canvas = photo(r)
        r['asset'] = str(canvas)
        run(FF + ['-loop', '1', '-framerate', '30', '-i', canvas, '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000', '-vf', 'setsar=1,format=yuv420p', '-map', '0:v:0', '-map', '1:a:0', '-frames:v', str(round(r['duration'] * FPS)), '-t', str(r['duration']), *ENC, '-c:a', 'pcm_s16le', out], f'segment-{key}')
    else:
        has_audio = any((s.get('codec_type') == 'audio' for s in probe(p)['streams']))
        silence_input = [] if has_audio else ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000']
        cap = W / 'assets' / f'{key}.txt'
        cap.write_text(r['caption'])
        tag = W / 'assets' / f'{key}-tag.txt'
        tag.write_text(r['category'])
        filt = f"[0:v]setpts=(PTS-STARTPTS)/{excerpt['speed']},split=2[b0][f0];[b0]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,gblur=sigma=32:steps=2,eq=brightness=-0.12:saturation=0.8[b];[f0]scale=1780:930:force_original_aspect_ratio=decrease:force_divisible_by=2[f];[b][f]overlay=(W-w)/2:(H-h)/2-22:shortest=1,fps=30,setsar=1,format=yuv420p,drawbox=x=0:y=996:w=1920:h=84:color=0x111923@0.7:t=fill,drawtext=fontfile={FONT}:textfile={cap}:fontsize={SIZES['caption']}:fontcolor=0xf4f0e8:x=62:y=1010,drawtext=fontfile={FONT}:textfile={tag}:fontsize={SIZES['category']}:fontcolor=0xd3bb91:x=w-tw-62:y=1014[v];[0:a]{atempo(excerpt['speed'])},aresample=48000,aformat=channel_layouts=stereo,volume=0.85,afade=t=in:d=0.15,afade=t=out:st=8.8:d=0.2[a]"
        if not has_audio:
            filt = filt.replace('[0:a]', '[1:a]')
        run(FF + ['-ss', str(excerpt['start_seconds']), '-t', str(excerpt['source_seconds']), '-i', p, *silence_input, '-filter_complex', filt, '-map', '[v]', '-map', '[a]', '-t', '9', *ENC, '-c:a', 'pcm_s16le', out], f'segment-{key}')
    if not abs(float(probe(out)['format']['duration']) - r['duration']) < 0.1:
        raise ValueError('Validation failed')

def card(name, title, sub, duration):
    out = W / 'segments' / f'card-{name}.mkv'
    canvas = W / 'assets' / f'card-{name}.jpg'
    if not canvas.exists():
        art = THEME.get(name)
        background = [art['path'], '-resize', '1920x1080!'] if art else ['-size', '1920x1080', 'xc:#f4eddf']
        title_size = SIZES['title']
        sub_size = SIZES['subtitle']

        def width(text, size):
            return int(subprocess.check_output(['magick', '-background', 'none', '-font', FONT, '-pointsize', str(size), 'label:' + text, '-format', '%w', 'info:'], text=True))
        while title_size > 88 and width(title, title_size) > 1680:
            title_size -= 2
        while sub_size > 38 and width(sub, sub_size) > 1680:
            sub_size -= 2
        if not (width(title, title_size) <= 1680 and width(sub, sub_size) <= 1680):
            raise ValueError('title text exceeds safe area')
        run(['magick', *background, '-font', FONT, '-fill', '#263d35', '-pointsize', str(title_size), '-gravity', 'center', '-annotate', '+0-60', title, '-fill', '#765c38', '-pointsize', str(sub_size), '-annotate', '+0+84', sub, '-pointsize', str(SIZES['ai_label']), '-gravity', 'southeast', '-annotate', '+55+30', '章节插画 · AI生成' if art else '家庭记忆', '-quality', '95', canvas], 'card-' + name)
    if not out.exists():
        run(FF + ['-loop', '1', '-framerate', '30', '-i', canvas, '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000', '-map', '0:v:0', '-map', '1:a:0', '-t', str(duration), '-vf', 'setsar=1,format=yuv420p', *ENC, '-c:a', 'pcm_s16le', out], 'title-' + name)
    return {'kind': 'title', 'segment': str(out), 'duration': duration, 'title': title}
try:
    ap = argparse.ArgumentParser()
    ap.add_argument('--prepare-only', action='store_true')
    a = ap.parse_args()
    rows = json.loads((W / 'selection.json').read_text())
    timing = plan(rows, DURATION_POLICY)
    (W / 'duration-plan.json').write_text(json.dumps(timing, ensure_ascii=False, indent=2))
    for r, seconds in zip(rows, timing['durations']):
        r['duration'] = seconds
    for i, r in enumerate(rows):
        segment(r)
        save(stage='segments', completed=i + 1, total=len(rows))
    (W / 'prepared-final-selection.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    if a.prepare_only:
        raise SystemExit(0)
    entries = [card('opening', '历史上的今天', CTX['opening_sub'], 4.0)]
    same = [r for r in rows if r['chapter'] == 'same-day']
    trip = [r for r in rows if r['chapter'] == 'trip']
    season = [r for r in rows if r['chapter'] == 'season']
    month = [r for r in rows if r['chapter'] == 'month-memory']
    if same:
        entries.append(card('same-day', CTX['same_day_title'], '同日回忆 · 保留每一年的真实拍摄日期', 3.5))
        entries.extend(same)
    if trip:
        locations = [r.get('location', '').split(' · ')[0] for r in trip if r.get('location')]
        place = max(set(locations), key=locations.count) if locations else '旅途'
        dates = sorted((r['capture_date'][:10] for r in trip))
        entries.append(card('trip', place + '的那些天', dates[0].replace('-', '.') + '—' + dates[-1].replace('-', '.') + ' · 行程延伸', 4.0))
        entries.extend(trip)
    if month:
        entries.append(card('month', '本月的记忆', '本月精选 · 以下不全是同一天的素材', 4.0))
        entries.extend(month)
    if season:
        entries.append(card('season', str(CTX['month']) + '月的自然片刻', '季节补景 · 来自家中图库的其他日期', 4.0))
        entries.extend(season)
    entries.append(dict(entries[0]))
    total = round(sum((e['duration'] for e in entries)) - (len(entries) - 1) * X, 3)
    if not abs(total - timing['duration']) < 0.05:
        raise ValueError('Validation failed')
    check_actual(total, DURATION_POLICY)
    (W / 'edit-decision-list.json').write_text(json.dumps({'entries': entries, 'duration': total, 'transition': X, 'presentation_version': STYLE['version'], 'matching_opening_ending': True, 'font_weight': STYLE['font_weight'], 'font_sizes': SIZES, 'photo_motion': 'none', 'music_embedded': False, 'original_clip_audio_retained': True}, ensure_ascii=False, indent=2))
    pieces = []
    for k, e in enumerate(entries):
        start = 0 if k == 0 else X
        dur = e['duration'] - start - (X if k < len(entries) - 1 else 0)
        out = W / 'pieces' / f'{k:03d}-body.mkv'
        if not out.exists():
            run(FF + ['-ss', str(start), '-i', e['segment'], '-t', str(dur), '-vf', 'fps=30,setsar=1,format=yuv420p', '-map', '0:v:0', '-map', '0:a:0', *ENC, '-c:a', 'pcm_s16le', out], f'body-{k:03d}')
        pieces.append(out)
        if k < len(entries) - 1:
            out = W / 'pieces' / f'{k:03d}-transition.mkv'
            if not out.exists():
                f = '[0:v]settb=AVTB,setpts=PTS-STARTPTS,format=yuv444p[a];[1:v]settb=AVTB,setpts=PTS-STARTPTS,format=yuv444p[b];[a][b]xfade=transition=fade:duration=0.7:offset=0,format=yuv420p[v];[0:a][1:a]acrossfade=d=0.7:c1=tri:c2=tri[au]'
                run(FF + ['-ss', str(e['duration'] - X), '-t', str(X), '-i', e['segment'], '-t', str(X), '-i', entries[k + 1]['segment'], '-filter_complex', f, '-map', '[v]', '-map', '[au]', '-t', str(X), *ENC, '-c:a', 'pcm_s16le', out], f'transition-{k:03d}')
            pieces.append(out)
        save(stage='transitions', completed=k + 1, total=len(entries))
    concat = W / 'programme.ffconcat'
    concat.write_text('ffconcat version 1.0\n' + ''.join(("file '" + str(p).replace("'", "'\\''") + "'\n" for p in pieces)))
    master_signal = next((v for v in probe(pieces[0])['streams'] if v.get('codec_type') == 'video'))
    matrix = {'bt470bg': '470bg', 'bt709': '709'}.get(master_signal.get('color_space'))
    value_range = {'pc': 'full', 'tv': 'limited'}.get(master_signal.get('color_range'))
    if not (matrix and value_range):
        raise ValueError('Unknown composite master colour signal; do not publish')
    final_filter = f'zscale=matrixin={matrix}:rangein={value_range}:matrix=709:range=limited:dither=error_diffusion,format=yuv420p,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709'
    out = W / 'daily-render.mp4'
    if not not out.exists():
        raise ValueError('Validation failed')
    tmp = W / 'today.partial.mp4'
    run(FF + ['-f', 'concat', '-safe', '0', '-i', concat, '-map', '0:v:0', '-map', '0:a:0', '-vf', final_filter, '-c:v', 'h264_nvenc', '-preset', 'p5', '-rc', 'vbr', '-cq', '20', '-b:v', '5M', '-maxrate', '8M', '-bufsize', '16M', '-profile:v', 'high', '-level:v', '4.1', '-pix_fmt', 'yuv420p', '-g', '60', '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-video_track_timescale', '90000', '-t', str(total), '-movflags', '+faststart', tmp], 'final')
    m = probe(tmp)
    if not abs(float(m['format']['duration']) - total) < 0.2:
        raise ValueError('Validation failed')
    v = next((v for v in m['streams'] if v.get('codec_type') == 'video'))
    if not (v.get('codec_name') == 'h264' and v.get('width') == 1920 and (v.get('height') == 1080) and (v.get('pix_fmt') == 'yuv420p') and (v.get('color_range') == 'tv') and (v.get('color_space') == 'bt709')):
        raise ValueError('TV colour specification failed; do not publish')
    check_actual(float(m['format']['duration']), DURATION_POLICY)
    run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '2', '-i', str(tmp), '-f', 'null', '-'], 'full-decode')
    for r in rows:
        st = Path(r['source_path']).stat()
        if not [st.st_size, st.st_mtime_ns] == r['original_stat']:
            raise ValueError('Validation failed')
    static_output = W / 'static-photo-validation.json'
    static_checker = B / 'daily-on-this-day/static-photo-check.py'
    subprocess.run([sys.executable, str(static_checker), '--selection', str(W / 'prepared-final-selection.json'), '--output', str(static_output)], check=True)
    static_result = json.loads(static_output.read_text())
    if not static_result['passed']:
        raise ValueError('photo content moved beyond codec tolerance')
    r = next((r for r in rows if r['kind'] == 'photo'))
    hs = []
    for sec in [1, 5]:
        h = subprocess.check_output(['ffmpeg', '-v', 'error', '-threads', '1', '-ss', str(sec), '-i', r['segment'], '-frames:v', '1', '-map', '0:v:0', '-an', '-f', 'hash', '-hash', 'sha256', '-'], text=True).strip()
        hs.append(h)
    tmp.rename(out)
    receipt = {'duration_policy': timing, 'presentation_version': STYLE['version'], 'matching_opening_ending': True, 'font_weight': STYLE['font_weight'], 'font_sizes': SIZES, 'status': 'rendered_verified', 'path': str(out), 'sha256': sha(out), 'bytes': out.stat().st_size, 'duration': float(m['format']['duration']), 'photos': sum((r['kind'] == 'photo' for r in rows)), 'video_clips': sum((r['kind'] == 'video' for r in rows)), 'years': sorted(set((r['year'] for r in rows))), 'full_decode_passed': True, 'source_stats_unchanged': True, 'photo_motion': 'none', 'representative_static_frame_hashes': hs, 'static_photo_validation': static_result, 'bgm_embedded': False, 'location_captions': True, 'seasonal_supplements_labelled': True, 'trip_selected_date_range': [min((r['capture_date'][:10] for r in trip)), max((r['capture_date'][:10] for r in trip))] if trip else [], 'video_original_audio_retained': True, 'probe': m}
    (W / 'render-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    save(stage='complete', **receipt)
except Exception as e:
    save(stage='failed', error=str(e), traceback=traceback.format_exc())
    raise
