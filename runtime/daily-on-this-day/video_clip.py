"""Bounded excerpt selection for original videos; no source duration ceiling."""
import json, math

def metadata(row):
    value = row.get('metadata_json') or {}
    return json.loads(value) if isinstance(value, str) else value

def source_duration(row):
    value = float(metadata(row).get('QuickTime:Duration') or 0)
    if not (math.isfinite(value) and value > 0):
        raise ValueError('Missing valid source video duration')
    return value

def clip_spec(row, seconds=9.0):
    total = source_duration(row)
    seconds = float(seconds)
    speed = float(row.get('video_speed', 1.0))
    if not (math.isfinite(speed) and 1.0 <= speed <= 4.0):
        raise ValueError('Video speed must be 1x to 4x')
    needed = seconds * speed
    if not (math.isfinite(seconds) and seconds > 0 and (total + 0.01 >= needed)):
        raise ValueError('Source shorter than requested excerpt')
    default = min(2, max(0, total - needed))
    start = float(row.get('video_start_seconds', default))
    if not (math.isfinite(start) and start >= 0 and (start + needed <= total + 0.01)):
        raise ValueError('Excerpt exceeds source bounds')
    return {'start_seconds': round(start, 6), 'speed': speed, 'source_seconds': round(needed, 6), 'output_seconds': seconds, 'source_duration_seconds': total}

def preview_times(row):
    total = source_duration(row)
    return [round(max(0, min(total - 0.1, total * f)), 3) for f in (0.2, 0.5, 0.8)]

def atempo(speed):
    parts = []
    while speed > 2.0:
        parts.append('atempo=2.0')
        speed /= 2.0
    parts.append('atempo=' + str(speed))
    return ','.join(parts)
