#!/usr/bin/env python3
"""Create an entirely synthetic library and complete local pipeline configuration."""
from pathlib import Path
import argparse, datetime as dt, json, os, subprocess, sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--font', required=True)
    parser.add_argument('--date', default='2030-10-24')
    parser.add_argument('--backend', choices=['nvenc', 'cpu'], default='nvenc')
    parser.add_argument('--music', action='store_true')
    args = parser.parse_args()
    dt.date.fromisoformat(args.date)
    root = Path(args.root).absolute()
    os.umask(0o077)
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    # Existing synthetic generator is project-owned; never reads a real photo library.
    subprocess.run([sys.executable, str(Path(__file__).with_name('make_synthetic.py')),
                    '--root', str(root / 'synthetic'), '--font', args.font, '--date', args.date, '--backend', args.backend], check=True)
    library = root / 'synthetic' / '合成 图片'
    selection = json.loads((root / 'synthetic' / 'selection.json').read_text())
    for i, row in enumerate(selection):
        (library / (row['file'] + '.mytv.json')).write_text(json.dumps({'schema': 1, 'capture_date': row['capture_date'],
             'location': {'label': '合成地点', 'confirmed': True}, 'people': [{'name': '合成标签', 'confirmed': True}]}, ensure_ascii=False))
    # Unknown date is not inferred from its newly generated mtime; duplicate is deduplicated by SHA.
    import shutil
    shutil.copyfile(library / selection[0]['file'], library / '未知日期.jpg')
    shutil.copyfile(library / selection[1]['file'], library / '重复副本.jpg')
    (library / '重复副本.jpg.mytv.json').write_text(json.dumps({'schema': 1, 'capture_date': selection[1]['capture_date']}))
    config = {'schema': 1, 'timezone': 'UTC', 'library_root': str(library), 'state_root': str(root / 'state'),
              'work_root': str(root / 'work'), 'publish_root': str(root / 'publish'), 'font_file': str(Path(args.font).absolute()),
              'encoding_backend': args.backend, 'selection': {'recent_days': 7, 'allow_same_month_fallback': True, 'leap_day_fallback': 'none'},
              'limits': {'timeout_seconds': 900, 'term_grace_seconds': 20, 'minimum_free_bytes': 5 * 1024 ** 3,
                         'maximum_output_bytes': 3 * 1024 ** 3, 'minimum_memory_bytes': 3 * 1024 ** 3,
                         'maximum_rss_bytes': 4 * 1024 ** 3, 'maximum_load1': 8}}
    if args.music:
        music = root / 'synthetic-music.wav'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-f', 'lavfi', '-i', 'sine=frequency=220:sample_rate=48000',
                        '-t', '3', '-c:a', 'pcm_s16le', str(music)], check=True, timeout=15)
        config['music'] = {'file': str(music), 'license_confirmed': True, 'volume': .12}
    (root / 'pipeline.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'synthetic_only': True, 'config': str(root / 'pipeline.json'), 'programme_day': args.date}))

if __name__ == '__main__':
    main()
