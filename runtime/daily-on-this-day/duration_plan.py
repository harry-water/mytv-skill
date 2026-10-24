"""Pure timing plan shared by curation, render and publication gates."""
import math
VERSION = 'daily-180-300-v1'

def plan(rows, cfg):
    if not rows:
        raise ValueError('No reviewed assets')
    lo, hi = cfg['target_duration_seconds']
    if not (lo, hi) == (180, 300):
        raise ValueError('Validation failed')
    timing = cfg['timing']
    x = float(cfg.get('transition_seconds', 0.7))
    fps = 30
    photos = sum((r['kind'] == 'photo' for r in rows))
    videos = len(rows) - photos
    chapters = {r['chapter'] for r in rows}
    cards = [4.0, 4.0] + [3.5 if c == 'same-day' else 4.0 for c in chapters]
    overhead = sum(cards) - (len(rows) + len(cards) - 1) * x
    vd = float(timing['video_clip_seconds'])
    pmin, pmax = timing['photo_seconds_range']
    natural_min = overhead + photos * pmin + videos * vd
    natural_max = overhead + photos * pmax + videos * vd
    if not (natural_max >= lo and natural_min <= hi):
        raise ValueError(('Reviewed selection cannot fit 180-300 seconds at comfortable pacing; select more or fewer distinct reviewed memories', len(rows), round(natural_min, 2), round(natural_max, 2)))
    desired = float(timing['preferred_duration_seconds'])
    psec = max(pmin, min(pmax, (desired - overhead - videos * vd) / photos)) if photos else pmin
    pframes = round(psec * fps)
    durations = [pframes / fps if r['kind'] == 'photo' else vd for r in rows]
    total = round(overhead + sum(durations), 3)
    if photos and total < lo:
        pframes += math.ceil((lo - total) * fps / photos)
    if photos and total > hi:
        pframes -= math.ceil((total - hi) * fps / photos)
    durations = [pframes / fps if r['kind'] == 'photo' else vd for r in rows]
    total = round(overhead + sum(durations), 3)
    if not (lo <= total <= hi and pmin <= pframes / fps <= pmax):
        raise ValueError((total, pframes / fps))
    return {'version': VERSION, 'duration': total, 'durations': durations, 'photo_seconds': pframes / fps, 'video_seconds': vd, 'range_seconds': [lo, hi], 'preferred_seconds': desired, 'unique_assets': len(rows), 'repeated_assets_to_fill': False}

def check_actual(seconds, cfg):
    if not cfg['target_duration_seconds'][0] <= float(seconds) <= cfg['target_duration_seconds'][1]:
        raise ValueError(f'Actual duration {seconds}s is outside 180-300s; publication rejected')
    return True
