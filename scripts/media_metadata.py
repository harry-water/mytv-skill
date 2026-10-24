"""Bounded, read-only media validation and explicit capture-time provenance."""
import datetime as dt
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
from zoneinfo import ZoneInfo

def check(ok, text):
    if not ok:
        raise ValueError(text)

def validate_decode(path, value, seconds=30):
    visual = next((s for s in value.get('streams', []) if s.get('codec_type') == 'video'), {})
    width, height = visual.get('width', 0), visual.get('height', 0)
    check(type(width) is int and type(height) is int and width > 0 and height > 0, 'Missing valid media dimensions')
    check(width * height <= 100_000_000, 'Media exceeds supported 100 megapixel validation bound')
    image = Path(path).suffix.lower() in ('.jpg', '.jpeg', '.png')
    if not image:
        duration = float(value.get('format', {}).get('duration', 0))
        check(math.isfinite(duration) and duration > 0, 'Invalid video duration')
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '1', '-err_detect', 'explode',
               '-protocol_whitelist', 'file,pipe', '-i', str(path), '-map', '0:v:0']
    command += ['-frames:v', '1', '-an'] if image else ['-map', '0:a:0?']
    command += ['-f', 'null', '-']
    subprocess.run(command, check=True, capture_output=True, timeout=seconds)

def read_photo_tags(path):
    if not shutil.which('exiftool'):
        return {'status': 'optional-exiftool-unavailable', 'tags': {}}
    # Disable user Perl configuration; request only capture-time/GPS fields, never owner/device identifiers.
    command = ['exiftool', '-config', '', '-j', '-n', '-G1', '-EXIF:DateTimeOriginal', '-EXIF:OffsetTimeOriginal',
               '-EXIF:CreateDate', '-EXIF:GPSLatitude', '-EXIF:GPSLongitude', '-EXIF:GPSLatitudeRef', '-EXIF:GPSLongitudeRef', str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    if result.returncode:
        return {'status': 'embedded-exif-extraction-error', 'tags': {}}
    try:
        values = json.loads(result.stdout)
        check(isinstance(values, list) and len(values) == 1 and isinstance(values[0], dict), 'Unexpected EXIF result')
        tags = {k: v for k, v in values[0].items() if k.startswith(('ExifIFD:', 'GPS:', 'IFD0:'))}
        return {'status': 'embedded-exif-read', 'tags': tags}
    except (ValueError, TypeError):
        return {'status': 'embedded-exif-extraction-error', 'tags': {}}

def time_candidate(source, raw, zone, offset=None, exif=False, confidence='embedded-unconfirmed'):
    item = {'source': source, 'raw': raw, 'capture_date': None, 'capture_instant': None,
            'confidence': 'unknown', 'status': 'invalid-time'}
    try:
        check(isinstance(raw, str), 'Time must be string')
        parsed = dt.datetime.strptime(raw, '%Y:%m:%d %H:%M:%S') if exif else dt.datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if exif and offset is not None:
            check(isinstance(offset, str) and (re.fullmatch(r'[+-]\d\d:\d\d', offset) or offset == 'Z'), 'Invalid EXIF offset')
            parsed = dt.datetime.fromisoformat(parsed.isoformat() + offset.replace('Z', '+00:00'))
        if parsed.utcoffset() is None:
            item.update(status='local-time-without-offset', local_time=parsed.isoformat())
        else:
            item.update(status='offset-time', capture_date=parsed.astimezone(ZoneInfo(zone)).date().isoformat(),
                        capture_instant=parsed.astimezone(dt.timezone.utc).isoformat(), confidence=confidence)
    except (ValueError, TypeError):
        pass
    return item

def exif_gps(tags):
    try:
        lat, lon = float(tags['GPS:GPSLatitude']), float(tags['GPS:GPSLongitude'])
        north, east = tags['GPS:GPSLatitudeRef'], tags['GPS:GPSLongitudeRef']
        check(north in ('N', 'S') and east in ('E', 'W'), 'Missing GPS hemisphere')
        lat = abs(lat) * (1 if north == 'N' else -1)
        lon = abs(lon) * (1 if east == 'E' else -1)
        check(math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180, 'Invalid GPS')
        return {'latitude': lat, 'longitude': lon, 'source': 'embedded-exif-gps', 'confidence': 'embedded'}
    except (KeyError, ValueError, TypeError):
        return None

def resolve(path, media, zone, supplied, params, video_policy='candidate'):
    if supplied.get('timezone') is not None:
        ZoneInfo(supplied['timezone'])
    record = {'capture_date': None, 'time_source': 'unknown', 'time_confidence': 'unknown', 'timezone': None,
              'location': None, 'people': [], 'gps': None, 'time_candidates': [], 'time_conflicts': [], 'gps_conflicts': [],
              'media': {'streams': params.get('streams', []), 'duration': params.get('format', {}).get('duration'),
                        'validation': 'positive-dimensions-and-strict-decode'}, 'metadata_capabilities': {}}
    candidates = record['time_candidates']
    embedded = []
    tags = {}
    if media == 'photo':
        result = read_photo_tags(path)
        record['metadata_capabilities']['photo_exif'] = result['status']
        tags = result['tags']
        if tags.get('ExifIFD:DateTimeOriginal') is not None:
            embedded.append(time_candidate('embedded-exif-original', tags['ExifIFD:DateTimeOriginal'], zone,
                                           tags.get('ExifIFD:OffsetTimeOriginal'), True, 'embedded-offset-original'))
        if tags.get('ExifIFD:CreateDate') is not None:
            hint = time_candidate('embedded-exif-digitization-hint', tags['ExifIFD:CreateDate'], zone, exif=True)
            hint['not_assumed_capture_time'] = True
            candidates.append(hint)
        record['gps'] = exif_gps(tags)
    else:
        tagged = [('container', params.get('format', {}).get('tags', {}))]
        tagged += [(f'stream-{i}', stream.get('tags', {})) for i, stream in enumerate(params.get('streams', []))]
        for name, values in tagged:
            if values.get('creation_time') is not None:
                embedded.append(time_candidate('embedded-video-' + name, values['creation_time'], zone,
                                confidence='embedded-video-offset-policy' if video_policy == 'use-offset' else 'embedded-unconfirmed'))
    chosen = None
    if 'capture_time' in supplied:
        chosen = time_candidate('sidecar-offset-time', supplied['capture_time'], zone, confidence='provided')
        if chosen['capture_instant'] and supplied.get('timezone'):
            parsed = dt.datetime.fromisoformat(supplied['capture_time'].replace('Z', '+00:00'))
            if parsed.utcoffset() != parsed.astimezone(ZoneInfo(supplied['timezone'])).utcoffset():
                record['time_conflicts'].append({'kind': 'sidecar-zone-offset-disagreement'})
                chosen.update(capture_date=None, confidence='unknown', status='conflict')
    elif 'capture_date' in supplied:
        date = dt.date.fromisoformat(supplied['capture_date'])
        chosen = {'source': 'sidecar-date', 'capture_date': date.isoformat(), 'capture_instant': None,
                  'confidence': 'provided-date-only', 'status': 'provided-calendar-date'}
    candidates.extend(embedded)
    if chosen is not None:
        candidates.insert(0, chosen)
        for item in embedded:
            if chosen['capture_date'] and item['capture_date'] and chosen['capture_date'] != item['capture_date']:
                record['time_conflicts'].append({'kind': 'sidecar-overrides-embedded-date', 'other_source': item['source']})
    elif embedded:
        chosen = embedded[0]
        instants = {x['capture_instant'] for x in embedded if x['capture_instant']}
        if len(instants) > 1:
            record['time_conflicts'].append({'kind': 'embedded-time-disagreement'})
            chosen = {**chosen, 'capture_date': None, 'confidence': 'unknown', 'status': 'conflict'}
    if chosen is not None:
        record.update(capture_date=chosen['capture_date'], time_source=chosen['source'], time_confidence=chosen['confidence'],
                      timezone='explicit-offset' if chosen.get('capture_instant') else None)
    side_gps = supplied.get('gps')
    if side_gps is not None:
        check(isinstance(side_gps, dict), 'gps must be an object')
        lat, lon = side_gps.get('latitude'), side_gps.get('longitude')
        check(type(lat) in (int, float) and type(lon) in (int, float) and math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180, 'Invalid sidecar GPS')
        if side_gps.get('confirmed') is True:
            old = record['gps']
            if old and (abs(old['latitude'] - lat) > 1e-6 or abs(old['longitude'] - lon) > 1e-6):
                record['gps_conflicts'].append({'kind': 'confirmed-sidecar-overrides-embedded-gps'})
            record['gps'] = {'latitude': lat, 'longitude': lon, 'source': 'confirmed-sidecar-gps', 'confidence': 'provided'}
    place = supplied.get('location')
    if place:
        check(isinstance(place, dict) and isinstance(place.get('label'), str), 'Invalid location label')
        if place.get('confirmed') is True:
            record['location'] = place['label'][:200]
    check(isinstance(supplied.get('people', []), list), 'people must be an array')
    for person in supplied.get('people', []):
        check(isinstance(person, dict) and isinstance(person.get('name'), str), 'Invalid person tag')
        if person.get('confirmed') is True:
            record['people'].append(person['name'][:100])
    return record
