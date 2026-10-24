#!/usr/bin/env python3
"""Deterministic local collect/edit/deliver pipeline. No cloud or device control."""
from pathlib import Path
import argparse, contextlib, datetime as dt, fcntl, hashlib, json, math, os, shutil, signal, stat, subprocess, sys, time, uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
INDEXER_VERSION = 2
sys.path.insert(0, str(ROOT / 'runtime/daily-on-this-day'))
from duration_plan import plan as duration_plan
from video_clip import clip_spec
GIB = 1024 ** 3
MEDIA = {'.jpg': 'photo', '.jpeg': 'photo', '.png': 'photo', '.mp4': 'video', '.mov': 'video', '.mkv': 'video'}
PAYLOADS = ('programme.mp4', 'cover.jpg')

def require(ok, message):
    if not ok:
        raise ValueError(message)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 ** 2), b''):
            h.update(block)
    return h.hexdigest()

def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def no_links(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    for part in [path, *path.parents]:
        require(not part.is_symlink(), 'Symlink path rejected')
    return path

def sync_dir(path):
    fd = os.open(no_links(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def private_dir(path):
    path = no_links(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    require(path.is_dir() and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) & 0o077 == 0,
            'Managed directory must be owned by caller and private (0700)')
    return path

def read_json(path, limit=16 * 1024 ** 2):
    path = no_links(path)
    require(path.is_file() and path.stat().st_size <= limit, 'Missing or oversized JSON')
    with path.open() as stream:
        return json.load(stream, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))

def atomic_bytes(path, data):
    path = no_links(path)
    tmp = path.parent / ('.pending-' + uuid.uuid4().hex)
    with tmp.open('xb') as stream:
        os.chmod(tmp, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def atomic_json(path, value):
    atomic_bytes(path, canonical(value))

def immutable_json(path, value):
    path = no_links(path)
    with path.open('xb') as output:
        os.chmod(path, 0o600)
        output.write(canonical(value))
        output.flush()
        os.fsync(output.fileno())
    sync_dir(path.parent)

@contextlib.contextmanager
def lock(root, name='pipeline.lock'):
    root = private_dir(root)
    fd = os.open(root / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield

def finite_number(value, lo, hi, name):
    require(type(value) in (int, float) and math.isfinite(value) and lo <= value <= hi, 'Invalid ' + name)
    return value

def load_config(path):
    cfg = read_json(path)
    require(cfg.get('schema') == 1, 'Config schema must be 1')
    ZoneInfo(cfg['timezone'])
    paths = {key: no_links(cfg[key]) for key in ('library_root', 'state_root', 'work_root', 'publish_root')}
    require(paths['library_root'].is_dir(), 'Local library missing')
    for i, a in enumerate(paths.values()):
        for b in list(paths.values())[i + 1:]:
            require(not a.is_relative_to(b) and not b.is_relative_to(a), 'Library/state/work/publish trees must be disjoint')
    cfg.update({key: str(value) for key, value in paths.items()})
    # User-specified external read-only fonts can be system-managed symlinks.
    cfg['font_file'] = str(Path(cfg['font_file']).expanduser().resolve(strict=True))
    require(Path(cfg['font_file']).is_file(), 'Font missing')
    require(cfg.get('encoding_backend', 'nvenc') in ('nvenc', 'cpu'), 'Unknown encoding backend')
    selection = cfg.setdefault('selection', {})
    finite_number(selection.setdefault('recent_days', 7), 0, 366, 'recent_days')
    require(type(selection['recent_days']) is int, 'recent_days must be integer')
    require(selection.setdefault('leap_day_fallback', 'none') in ('none', 'feb28'), 'Unknown leap-day policy')
    require(type(selection.setdefault('allow_same_month_fallback', True)) is bool, 'Fallback must be boolean')
    for key in ('preferred_locations', 'preferred_people'):
        require(isinstance(selection.setdefault(key, []), list) and all(isinstance(x, str) and 0 < len(x) <= 200 for x in selection[key]), 'Invalid confirmed-tag preference')
    limits = cfg.setdefault('limits', {})
    for key, default, lo, hi in [('timeout_seconds', 900, 1, 3600), ('term_grace_seconds', 20, 1, 20),
                                 ('minimum_free_bytes', 5 * GIB, 1024 ** 2, 1024 * GIB),
                                 ('maximum_output_bytes', 3 * GIB, 1024 ** 2, 1024 * GIB),
                                 ('minimum_memory_bytes', 3 * GIB, 0, 1024 * GIB),
                                 ('maximum_rss_bytes', 4 * GIB, 1024 ** 2, 1024 * GIB),
                                 ('maximum_load1', 8, 0.1, 1024)]:
        finite_number(limits.setdefault(key, default), lo, hi, key)
    metadata_policy = cfg.setdefault('metadata', {})
    require(isinstance(metadata_policy, dict) and metadata_policy.setdefault('video_creation_time_policy', 'candidate') in ('candidate', 'use-offset'), 'Unknown video metadata policy')
    music = cfg.get('music')
    if music:
        require(music.get('license_confirmed') is True, 'Music requires confirmed right to use')
        music['file'] = str(no_links(music['file']))
        require(Path(music['file']).is_file(), 'Music missing')
        finite_number(music.setdefault('volume', 0.18), 0, 1, 'music volume')
    # Reuse the renderer's actual path policy, without executing media tools.
    import run as renderer
    renderer.validate_filter_path(cfg['font_file'])
    renderer.validate_filter_path(cfg['work_root'])
    return cfg

def preflight(cfg):
    checked = {}
    for key in ('state_root', 'work_root', 'publish_root'):
        p = Path(cfg[key])
        while not p.exists():
            p = p.parent
        free = shutil.disk_usage(p).free
        require(free >= cfg['limits']['minimum_free_bytes'], 'Insufficient free space for ' + key)
        checked[key] = {'free_bytes': free}
    if cfg.get('music'):
        result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=codec_type:format=duration',
                                 '-of', 'json', cfg['music']['file']], capture_output=True, text=True, check=True, timeout=30)
        audio = json.loads(result.stdout)
        require(bool(audio.get('streams')) and float(audio.get('format', {}).get('duration', 0)) > 0, 'Invalid music audio')
    return {'configuration_valid': True, 'storage': checked,
            'scope': 'Configuration and storage only; doctor probes dependencies separately; plan validates programme feasibility'}

def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries',
                             'format=duration:format_tags=creation_time:stream=codec_type,codec_name,width,height:stream_tags=creation_time',
                             '-of', 'json', str(path)], capture_output=True, text=True, timeout=30, check=True)
    value = json.loads(result.stdout)
    from media_metadata import validate_decode
    validate_decode(path, value)
    return value

def metadata(path, media, timezone, sidecar, video_policy='candidate'):
    from media_metadata import resolve
    supplied = read_json(sidecar, 65536) if sidecar.exists() else {}
    require(not supplied or type(supplied.get('schema')) is int and supplied['schema'] == 1, 'Unsupported sidecar schema')
    return resolve(path, media, timezone, supplied, probe(path), video_policy)

def index_library(cfg):
    state = private_dir(cfg['state_root'])
    old = read_json(state / 'index.json') if (state / 'index.json').exists() else {'records': []}
    prior = {r['file']: r for r in old['records']} if old.get('timezone') == cfg['timezone'] and old.get('indexer_version') == INDEXER_VERSION and old.get('metadata_policy') == cfg['metadata'] and old.get('exif_tool_available') == bool(shutil.which('exiftool')) else {}
    records, errors, reused, hashed = [], [], 0, 0
    library = Path(cfg['library_root'])
    for directory, dirs, names in os.walk(library, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            media = MEDIA.get(path.suffix.lower())
            if not media:
                continue
            relative = str(path.relative_to(library))
            try:
                no_links(path)
                require(path.is_file(), 'Not regular media')
                info = path.stat()
                sidecar = path.with_name(path.name + '.mytv.json')
                side_hash = sha(no_links(sidecar)) if sidecar.exists() else None
                signature = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, side_hash]
                existing = prior.get(relative)
                if existing and existing['signature'] == signature:
                    records.append(existing)
                    reused += 1
                    continue
                h = sha(path)
                record = dict(file=relative, kind=media, sha256=h, bytes=info.st_size, signature=signature,
                              **metadata(path, media, cfg['timezone'], sidecar, cfg['metadata']['video_creation_time_policy']))
                require(path.stat().st_size == info.st_size and path.stat().st_mtime_ns == info.st_mtime_ns, 'Source changed during indexing')
                records.append(record)
                hashed += 1
            except (OSError, ValueError, KeyError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
                errors.append({'file': relative, 'error_type': type(exc).__name__})
    value = {'schema': 1, 'indexer_version': INDEXER_VERSION, 'metadata_policy': cfg['metadata'], 'exif_tool_available': bool(shutil.which('exiftool')), 'timezone': cfg['timezone'], 'records': records, 'errors': errors,
             'incremental': {'reused': reused, 'hashed': hashed},
             'time_rule': 'mtime is a cache hint only, never capture time; stage verifies every selected SHA'}
    atomic_json(state / 'index.json', value)
    return value

def history(cfg):
    p = Path(cfg['state_root']) / 'history.json'
    return read_json(p) if p.exists() else {'schema': 1, 'events': []}

def make_plan(cfg, day, index=None):
    day = dt.date.fromisoformat(day)
    index = index if index is not None else read_json(Path(cfg['state_root']) / 'index.json')
    require(index.get('schema') == 1 and index.get('timezone') == cfg['timezone'], 'Reindex for current timezone/schema')
    require(index.get('indexer_version') == INDEXER_VERSION and index.get('metadata_policy') == cfg['metadata'] and index.get('exif_tool_available') == bool(shutil.which('exiftool')), 'Reindex for current validation/metadata policy')
    events = history(cfg)['events']
    recent = {h for e in events if 0 <= (day - dt.date.fromisoformat(e.get('event_day', e['day']))).days <= cfg['selection']['recent_days'] for h in e['input_sha256']}
    candidates, skipped = [], {'unknown_time': 0, 'recent': 0, 'not_prior_year_or_month': 0, 'duplicates': 0}
    seen = set()
    for record in sorted(index['records'], key=lambda r: r['file']):
        if not record.get('capture_date') or record['time_confidence'] == 'embedded-unconfirmed':
            skipped['unknown_time'] += 1
            continue
        capture = dt.date.fromisoformat(record['capture_date'])
        if capture.year >= day.year or capture.month != day.month:
            skipped['not_prior_year_or_month'] += 1
            continue
        if record['sha256'] in recent:
            skipped['recent'] += 1
            continue
        if record['sha256'] in seen:
            skipped['duplicates'] += 1
            continue
        same = (capture.month, capture.day) == (day.month, day.day)
        leap = day.month == 2 and day.day == 29 and (capture.month, capture.day) == (2, 28) and cfg['selection']['leap_day_fallback'] == 'feb28'
        if not same and not leap and not cfg['selection']['allow_same_month_fallback']:
            continue
        seen.add(record['sha256'])
        reason = 'historical-same-day' if same else ('explicit-leap-day-feb28' if leap else 'same-month-fallback')
        tag_score = int(record['location'] in cfg['selection'].get('preferred_locations', [])) + len(set(record['people']) & set(cfg['selection'].get('preferred_people', [])))
        rank = (0 if same else 1 if leap else 2, -tag_score, abs(capture.day - day.day), -capture.year, record['sha256'])
        candidates.append((rank, record, reason))
    candidates.sort(key=lambda x: x[0])
    rows, sources, explanations, timing = [], [], [], None
    policy = read_json(ROOT / 'config/duration.json')
    for _, record, reason in candidates:
        if len(rows) >= 46:
            break
        row = {'file': record['file'], 'kind': record['kind'], 'capture_date': record['capture_date'],
               'chapter': 'same-day' if reason == 'historical-same-day' else 'month-memory'}
        if row['kind'] == 'video':
            row.update(video_start_seconds=0, video_speed=1)
            try:
                clip_spec({'metadata_json': {'QuickTime:Duration': float(record['media']['duration'])}, **row})
            except (ValueError, TypeError, KeyError):
                continue
        rows.append(row)
        sources.append({'file': record['file'], 'sha256': record['sha256'], 'bytes': record['bytes']})
        explanations.append({'sha256': record['sha256'], 'reason': reason,
                             'time_source': record['time_source'], 'time_confidence': record['time_confidence'],
                             'confirmed_location_available': bool(record['location']), 'confirmed_person_tags': len(record['people']),
                             'preference_matches': int(record['location'] in cfg['selection'].get('preferred_locations', [])) + len(set(record['people']) & set(cfg['selection'].get('preferred_people', [])))})
        if any(r['kind'] == 'photo' for r in rows):
            try:
                timing = duration_plan(rows, policy)
            except ValueError:
                timing = None
            if timing:
                break
    require(timing is not None, 'Insufficient eligible distinct material for 180–300 seconds; previous programme preserved')
    closure = {str(p.relative_to(ROOT)): sha(p) for folder in ('scripts', 'runtime', 'config') for p in sorted((ROOT / folder).rglob('*')) if p.suffix in ('.py', '.json')}
    value = {'schema': 1, 'day': day.isoformat(), 'timezone': cfg['timezone'], 'selection': rows, 'sources': sources,
             'explanations': explanations, 'skipped': {**skipped, 'index_errors': len(index.get('errors', []))}, 'duration_plan': timing,
             'selection_policy': cfg['selection'], 'backend': cfg.get('encoding_backend', 'nvenc'),
             'font_sha256': sha(cfg['font_file']), 'music': {'sha256': sha(cfg['music']['file']), 'volume': cfg['music']['volume']} if cfg.get('music') else None,
             'code_closure': closure, 'limitations': ['confirmed-tag preference ranking only; no face recognition or GPS inference', 'date-only captions']}
    value['programme_id'] = digest(value)
    return value

def copy_verified(source, target, expected, expected_bytes):
    source, target = no_links(source), no_links(target)
    require(source.is_file() and source.stat().st_size == expected_bytes, 'Source size changed')
    pending = target.parent / ('.copy-' + uuid.uuid4().hex)
    with source.open('rb') as inp, pending.open('xb') as out:
        os.chmod(pending, 0o600)
        shutil.copyfileobj(inp, out, 1024 ** 2)
        out.flush()
        os.fsync(out.fileno())
    require(sha(pending) == expected and sha(source) == expected, 'Source/copy SHA mismatch; original preserved')
    os.link(pending, target)  # Atomic no-replace publication within the destination filesystem.
    pending.unlink()

def stage(cfg, plan):
    require(len(plan['selection']) == len(plan['sources']) > 0, 'Incomplete plan sources')
    require(plan['programme_id'] == digest({k: v for k, v in plan.items() if k != 'programme_id'}), 'Plan integrity mismatch')
    root = private_dir(Path(cfg['work_root']) / plan['programme_id'])
    inputs = private_dir(root / 'inputs')
    selected = []
    for i, (row, source) in enumerate(zip(plan['selection'], plan['sources'])):
        original = no_links(Path(cfg['library_root']) / source['file'])
        require(original.is_relative_to(Path(cfg['library_root'])), 'Invalid staged source path')
        name = f'{i:03d}' + original.suffix.lower()
        target = inputs / name
        if target.exists():
            require(target.is_file() and sha(target) == source['sha256'] and sha(original) == source['sha256'], 'Existing staged copy changed')
        else:
            copy_verified(original, target, source['sha256'], source['bytes'])
        selected.append({**row, 'file': name})
    atomic_json(root / 'plan.json', plan)
    atomic_json(root / 'selection.json', selected)
    return root

def child(args, log, timeout=900):
    # Same process group as the owned pipeline worker; monitor also tracks descendants.
    with no_links(log).open('a') as output:
        subprocess.run(args, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=timeout)

def strict_decode(path, log):
    child(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '2', '-i', str(path), '-f', 'null', '-'], log)

def valid_edition(root, manifest):
    require(manifest.get('schema') == 1 and len(manifest.get('files', [])) == 2, 'Unsupported publication manifest')
    require({x['name'] for x in manifest['files']} == set(PAYLOADS), 'Publication file allowlist mismatch')
    for record in manifest['files']:
        require(type(record['bytes']) is int and 0 < record['bytes'] <= 512 * 1024 ** 2, 'Invalid publication size')
        require(isinstance(record['sha256'], str) and len(record['sha256']) == 64 and all(c in '0123456789abcdef' for c in record['sha256']), 'Invalid SHA')
        path = no_links(root / record['name'])
        require(path.is_file() and path.stat().st_size == record['bytes'] and sha(path) == record['sha256'], 'Published media verification failed')

def activate(root, pointer, day):
    current = root / 'current.json'
    if current.exists():
        old = read_json(current, 65536)
        identity = old.get('programme_id', '')
        require(isinstance(identity, str) and len(identity) == 64 and all(c in '0123456789abcdef' for c in identity), 'Invalid current pointer')
        old_root = no_links(root / 'editions' / identity)
        require(sha(old_root / 'manifest.json') == old['manifest_sha256'], 'Current manifest corrupt; preserve for review')
        old_manifest = read_json(old_root / 'manifest.json', 65536)
        valid_edition(old_root, old_manifest)
        require(dt.date.fromisoformat(day) >= dt.date.fromisoformat(old_manifest['day']), 'Newer programme remains active; implicit rollback rejected')
        if old == pointer:
            return
    atomic_json(current, pointer)

def publish(cfg, plan, movie, cover):
    root = private_dir(cfg['publish_root'])
    with lock(root, 'publish.lock'):
        editions = private_dir(root / 'editions')
        final = editions / plan['programme_id']
        if not final.exists():
            temporary = private_dir(editions / ('.pending-' + uuid.uuid4().hex))
            records = []
            for source, name in [(movie, 'programme.mp4'), (cover, 'cover.jpg')]:
                info = {'name': name, 'bytes': source.stat().st_size, 'sha256': sha(source)}
                copy_verified(source, temporary / name, info['sha256'], info['bytes'])
                records.append(info)
            manifest = {'schema': 1, 'programme_id': plan['programme_id'], 'day': plan['day'],
                        'title': '历史上的今天', 'files': records, 'music_embedded': plan['music'] is not None,
                        'quality': {'strict_full_decode': True, 'static_photo_gate': True, 'cover_gate': True},
                        'privacy': 'No source paths, location/person tags or media metadata in delivery manifest'}
            atomic_json(temporary / 'manifest.json', manifest)
            valid_edition(temporary, manifest)
            os.rename(temporary, final)
        manifest = read_json(final / 'manifest.json', 65536)
        valid_edition(final, manifest)
        require(manifest['programme_id'] == plan['programme_id'], 'Existing edition identifier mismatch')
        sync_dir(editions)  # Also re-sync a prior rename interrupted before its directory fsync.
        pointer = {'schema': 1, 'programme_id': plan['programme_id'], 'manifest_sha256': sha(final / 'manifest.json')}
        activate(root, pointer, plan['day'])
    return pointer

def fetch(source, destination):
    source = no_links(source)
    destination = no_links(destination)
    require(not destination.is_relative_to(source) and not source.is_relative_to(destination), 'Publisher/player must be disjoint')
    destination = private_dir(destination)
    with lock(destination, 'fetch.lock'):
        pointer = read_json(source / 'current.json', 65536)
        require(pointer.get('schema') == 1 and isinstance(pointer.get('programme_id'), str) and len(pointer['programme_id']) == 64 and all(c in '0123456789abcdef' for c in pointer['programme_id']), 'Invalid publication pointer')
        edition = no_links(source / 'editions' / pointer['programme_id'])
        require(sha(edition / 'manifest.json') == pointer['manifest_sha256'], 'Manifest hash mismatch')
        manifest = read_json(edition / 'manifest.json', 65536)
        require(manifest['programme_id'] == pointer['programme_id'], 'Manifest identifier mismatch')
        valid_edition(edition, manifest)
        versions = private_dir(destination / 'editions')
        final = versions / pointer['programme_id']
        if not final.exists():
            temporary = private_dir(versions / ('.pending-' + uuid.uuid4().hex))
            for item in manifest['files']:
                copy_verified(edition / item['name'], temporary / item['name'], item['sha256'], item['bytes'])
            atomic_bytes(temporary / 'manifest.json', (edition / 'manifest.json').read_bytes())
            require(sha(temporary / 'manifest.json') == pointer['manifest_sha256'], 'Transfer manifest changed')
            valid_edition(temporary, manifest)
            os.rename(temporary, final)
        require(sha(final / 'manifest.json') == pointer['manifest_sha256'], 'Existing player manifest mismatch')
        valid_edition(final, manifest)
        sync_dir(versions)
        activate(destination, pointer, manifest['day'])
    return {'status': 'verified_and_switched', 'programme_id': pointer['programme_id'], 'playback_tested': False}

def note_history(cfg, plan, event):
    value = history(cfg)
    if not any(e['programme_id'] == plan['programme_id'] and e['event'] == event for e in value['events']):
        value['events'].append({'programme_id': plan['programme_id'], 'day': plan['day'], 'event': event,
                                'event_day': dt.datetime.now(ZoneInfo(cfg['timezone'])).date().isoformat() if event == 'played' else plan['day'],
                                'input_sha256': [x['sha256'] for x in plan['sources']]})
        atomic_json(Path(cfg['state_root']) / 'history.json', value)

def reconcile_movie(root, movie, raw, render_receipt):
    path = root / 'programme-receipt.json'
    if not path.exists():
        # Known crash window: movie rename completed but durable receipt did not.
        require(movie.is_file() and sha(raw) == render_receipt['sha256'], 'Interrupted programme input mismatch')
        media = probe(movie)
        require(abs(float(media['format']['duration']) - render_receipt['duration']) < .2, 'Interrupted programme duration mismatch')
        strict_decode(movie, root / 'reconcile-decode.log')
        atomic_json(path, {'sha256': sha(movie), 'strict_decode': True, 'raw_render_sha256': render_receipt['sha256'],
                          'reconciled_after_interruption': True})
    value = read_json(path)
    require(value['raw_render_sha256'] == render_receipt['sha256'] and sha(movie) == value['sha256'] and value['strict_decode'], 'Programme receipt mismatch')
    return value

def work(cfg, day):
    preflight(cfg)
    with lock(cfg['state_root']):
        journal_path = Path(cfg['state_root']) / ('run-' + day + '.json')
        prior = read_json(journal_path) if journal_path.exists() else None
        if prior and prior.get('status') in ('published', 'staged', 'rendered', 'failed'):
            plan = read_json(Path(cfg['work_root']) / prior['programme_id'] / 'plan.json')
            # Reconcile durable prior intent instead of selecting another programme after interruption.
            require(plan['day'] == day and plan['timezone'] == cfg['timezone'], 'Prior intent differs; inspect before retry')
            current_config = {k: v for k, v in cfg.items() if k != 'limits'}
            require(prior['config_sha256'] == digest(current_config), 'Config changed after intent; preserve work for review')
            closure = {str(p.relative_to(ROOT)): sha(p) for folder in ('scripts', 'runtime', 'config') for p in sorted((ROOT / folder).rglob('*')) if p.suffix in ('.py', '.json')}
            require(plan['code_closure'] == closure and plan['font_sha256'] == sha(cfg['font_file']), 'Code/font changed after intent; preserve for review')
            require(plan['music'] is None or plan['music']['sha256'] == sha(cfg['music']['file']), 'Music changed after intent')
        else:
            index = index_library(cfg)
            plan = make_plan(cfg, day, index)
        root = stage(cfg, plan)
        journal = {'schema': 1, 'day': day, 'programme_id': plan['programme_id'], 'status': 'staged',
                   'config_sha256': digest({k: v for k, v in cfg.items() if k != 'limits'})}
        atomic_json(journal_path, journal)
        try:
            published = Path(cfg['publish_root']) / 'editions' / plan['programme_id'] / 'manifest.json'
            if published.exists():
                pointer = publish(cfg, plan, root / 'programme.mp4', root / 'unused-cover.jpg')
                note_history(cfg, plan, 'published')
                journal.update(status='published', reused=True)
                atomic_json(journal_path, journal)
                return pointer
            render_cfg = {'font_file': cfg['font_file'], 'timezone': cfg['timezone'], 'input_root': str(root / 'inputs'),
                          'output_root': str(root / 'render'), 'encoding_backend': cfg.get('encoding_backend', 'nvenc')}
            atomic_json(root / 'render-config.json', render_cfg)
            output = root / 'render' / day
            if (root / 'render').exists() and not (output / 'adapter-receipt.json').exists():
                require(not (root / 'interrupted-render').exists(), 'Second partial render; retained for review')
                os.rename(root / 'render', root / 'interrupted-render')
            child([sys.executable, str(ROOT / 'scripts/run.py'), 'render', '--config', str(root / 'render-config.json'),
                   '--selection', str(root / 'selection.json'), '--date', day], root / 'render.log')
            receipt = read_json(output / 'render-receipt.json')
            require(receipt['full_decode_passed'] and receipt['static_photo_validation']['passed'], 'Renderer quality gate failed')
            presentation = read_json(output / 'daily-render.presentation.json')
            require(presentation['cover_decode_passed'], 'Cover quality gate failed')
            raw = output / 'daily-render.mp4'
            require(sha(raw) == receipt['sha256'], 'Render SHA changed')
            movie = root / 'programme.mp4'
            if not movie.exists():
                pending = root / ('movie-' + uuid.uuid4().hex + '.mp4')
                if cfg.get('music'):
                    music = cfg['music']
                    require(sha(music['file']) == plan['music']['sha256'], 'Music changed since plan')
                    child(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '2', '-filter_complex_threads', '1',
                           '-i', str(raw), '-stream_loop', '-1', '-i', music['file'], '-filter_complex',
                           f"[1:a]volume={music['volume']}[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
                           '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                           '-t', str(receipt['duration']), '-movflags', '+faststart', str(pending)], root / 'music.log')
                else:
                    copy_verified(raw, pending, receipt['sha256'], raw.stat().st_size)
                strict_decode(pending, root / 'final-decode.log')
                os.rename(pending, movie)
                atomic_json(root / 'programme-receipt.json', {'sha256': sha(movie), 'strict_decode': True,
                            'music_embedded': cfg.get('music') is not None, 'raw_render_sha256': receipt['sha256']})
            if cfg.get('music'):
                require(sha(cfg['music']['file']) == plan['music']['sha256'], 'Music changed since plan')
            reconcile_movie(root, movie, raw, receipt)
            journal['status'] = 'rendered'
            atomic_json(journal_path, journal)
            # Recheck source bytes before publication, including a resumed operation.
            stage(cfg, plan)
            pointer = publish(cfg, plan, movie, output / 'daily-render-thumb.jpg')
            note_history(cfg, plan, 'published')
            journal['status'] = 'published'
            atomic_json(journal_path, journal)
            return pointer
        except BaseException as exc:
            journal.update(status='failed', error_type=type(exc).__name__)
            atomic_json(journal_path, journal)
            raise

def proc_tree():
    result = {}
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            raw = (p / 'stat').read_text()
            parts = raw[raw.rfind(')') + 2:].split()
            result[int(p.name)] = {'ppid': int(parts[1]), 'start': int(parts[19]), 'rss': int(parts[21]) * os.sysconf('SC_PAGE_SIZE')}
        except (OSError, ValueError, IndexError):
            pass
    return result

def resource_sample(cfg):
    available = int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1]) * 1024
    pressure = {}
    for name in ('memory', 'io'):
        pressure[name] = float(Path('/proc/pressure', name).read_text().splitlines()[0].split('avg10=')[1].split()[0])
    return available, os.getloadavg()[0], pressure

def supervised(config_path, cfg, day):
    import resource
    preflight(cfg)
    available, load, psi = resource_sample(cfg)
    require(available >= cfg['limits']['minimum_memory_bytes'] and load < cfg['limits']['maximum_load1'] and max(psi.values()) < 5, 'Busy host; no task started')
    require(shutil.which('ffmpeg') and shutil.which('ffprobe') and shutil.which('magick'), 'Dependencies missing')
    state = private_dir(cfg['state_root'])
    # Parent admission lock prevents two monitor instances; worker uses a distinct business lock.
    with lock(state, 'admission.lock'):
        attempts = private_dir(private_dir(state / 'attempts') / day)
        attempt = attempts / uuid.uuid4().hex
        attempt.mkdir(mode=0o700, exist_ok=False)
        immutable_json(attempt / 'intent.json', {'schema': 1, 'day': day, 'started_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'attempt_id': attempt.name})
        log = attempt / 'supervisor.log'
        env = dict(os.environ, MAGICK_THREAD_LIMIT='2', OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MYTV_PIPELINE_OWNED='1')
        def bounds():
            os.umask(0o077)
            resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024 ** 2, 512 * 1024 ** 2))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        with log.open('x') as output:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '_worker', '--config', str(config_path), '--date', day],
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True, env=env, preexec_fn=bounds)
            known, samples, reason = {}, [], None
            begin = time.monotonic()
            stopping = False
            def stop(_sig, _frame):
                nonlocal stopping
                stopping = True
            old_handlers = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
            def owned(snapshot):
                roots = {p for p, start in known.items() if snapshot.get(p, {}).get('start') == start}
                if process.pid not in known and process.poll() is None and process.pid in snapshot:
                    roots.add(process.pid)
                while True:
                    added = {p for p, info in snapshot.items() if info['ppid'] in roots} - roots
                    if not added:
                        break
                    roots |= added
                for p in roots:
                    if p in snapshot:
                        known[p] = snapshot[p]['start']
                return {p for p, start in known.items() if snapshot.get(p, {}).get('start') == start}
            def terminate(sig):
                # Process group covers immediate descendants; verified PID tracking covers renderer's own session.
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, sig)
                    except ProcessLookupError:
                        pass
                snapshot = proc_tree()
                for p in owned(snapshot):
                    try:
                        os.kill(p, sig)
                    except ProcessLookupError:
                        pass
            try:
                while True:
                    snap = proc_tree()
                    pids = owned(snap)
                    rss = sum(snap[p]['rss'] for p in pids)
                    available, load, psi = resource_sample(cfg)
                    total = 0
                    for key in ('state_root', 'work_root', 'publish_root'):
                        for file in Path(cfg[key]).rglob('*'):
                            try:
                                if file.is_file() and not file.is_symlink():
                                    total += file.stat().st_size
                            except FileNotFoundError:
                                pass
                    elapsed = time.monotonic() - begin
                    samples.append({'elapsed': round(elapsed, 2), 'rss_bytes': rss, 'work_bytes': total,
                                    'memavailable_bytes': available, 'load1': load, 'psi': psi})
                    if stopping:
                        reason = 'caller_stop'
                    elif elapsed >= cfg['limits']['timeout_seconds']:
                        reason = 'whole_tree_timeout'
                    elif rss > cfg['limits']['maximum_rss_bytes'] or total > cfg['limits']['maximum_output_bytes']:
                        reason = 'task_resource_budget'
                    elif available < cfg['limits']['minimum_memory_bytes'] or max(psi.values()) >= 5 or load >= cfg['limits']['maximum_load1']:
                        reason = 'host_pressure'
                    elif any(shutil.disk_usage(Path(cfg[k]) if Path(cfg[k]).exists() else Path(cfg[k]).parent).free < cfg['limits']['minimum_free_bytes'] // 2 for k in ('state_root', 'work_root', 'publish_root')):
                        reason = 'disk_pressure'
                    if reason or process.poll() is not None:
                        break
                    time.sleep(0.5)
            finally:
                if reason or process.poll() is None or owned(proc_tree()):
                    terminate(signal.SIGTERM)
                    deadline = time.monotonic() + cfg['limits']['term_grace_seconds']
                    while time.monotonic() < deadline and owned(proc_tree()):
                        process.poll()
                        time.sleep(.1)
                    terminate(signal.SIGKILL)
                rc = process.wait(timeout=3)
                for s, handler in old_handlers.items():
                    signal.signal(s, handler)
                receipt = {'schema': 1, 'attempt_id': attempt.name, 'elapsed_seconds': round(time.monotonic() - begin, 3),
                            'returncode': rc, 'stop_reason': reason, 'limits': cfg['limits'], 'samples': samples,
                            'surviving_owned_pids': sorted(owned(proc_tree())), 'hard_file_limit_bytes': 512 * 1024 ** 2,
                            'aggregate_limits': 'monitored at 0.5 seconds, not hard quotas', 'scope': 'only this task descendants; never arbitrary host processes'}
                immutable_json(attempt / 'resource.json', receipt)
                atomic_json(state / ('resource-' + day + '.json'), receipt)
            require(reason is None and rc == 0, 'Pipeline stopped/failed; reconcile durable day journal; previous publication preserved')
    return read_json(Path(cfg['publish_root']) / 'current.json')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['preflight', 'index', 'plan', 'run', '_worker', 'fetch', 'played'])
    parser.add_argument('--config')
    parser.add_argument('--date')
    parser.add_argument('--source')
    parser.add_argument('--player-root')
    parser.add_argument('--programme-id')
    parser.add_argument('--confirmed-played', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == 'fetch':
        require(args.source and args.player_root, 'fetch needs source and player-root')
        print(json.dumps(fetch(args.source, args.player_root)))
        return
    require(args.config, 'Configuration required')
    config_path = no_links(args.config)
    cfg = load_config(config_path)
    day = args.date or dt.datetime.now(ZoneInfo(cfg['timezone'])).date().isoformat()
    dt.date.fromisoformat(day)
    if args.command == 'preflight':
        result = preflight(cfg)
    elif args.command == 'index':
        with lock(cfg['state_root']):
            value = index_library(cfg)
            result = {'records': len(value['records']), 'errors': value['errors'], 'incremental': value['incremental']}
    elif args.command == 'plan':
        with lock(cfg['state_root']):
            result = make_plan(cfg, day)
    elif args.command == '_worker':
        result = work(cfg, day)
    elif args.command == 'run':
        result = supervised(config_path, cfg, day)
    else:
        require(args.confirmed_played and args.programme_id, 'played requires explicit playback confirmation and programme-id')
        require(len(args.programme_id) == 64 and all(c in '0123456789abcdef' for c in args.programme_id), 'Invalid programme-id')
        with lock(cfg['state_root']):
            plan = read_json(Path(cfg['work_root']) / args.programme_id / 'plan.json')
            note_history(cfg, plan, 'played')
            result = {'recorded': 'caller-confirmed playback', 'not_a_device_probe': True}
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
