"""Small offline behaviour tests; mocked rendering is not an actual film acceptance."""
import datetime as dt, importlib.util, json, os, pathlib, signal, subprocess, sys, tempfile, unittest
from unittest.mock import patch
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pipeline as p

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mytv-pipeline-')
        self.root = pathlib.Path(self.tmp.name)
        (self.root / 'library').mkdir()
        (self.root / 'font.otf').write_bytes(b'synthetic font fixture')
        self.config = self.root / 'config.json'
        value = {'schema': 1, 'timezone': 'UTC', 'library_root': str(self.root / 'library'),
                 'state_root': str(self.root / 'state'), 'work_root': str(self.root / 'work'),
                 'publish_root': str(self.root / 'publisher'), 'font_file': str(self.root / 'font.otf'), 'encoding_backend': 'cpu',
                 'selection': {'recent_days': 7}, 'limits': {'minimum_free_bytes': 1024 ** 2, 'minimum_memory_bytes': 0, 'maximum_load1': 1024}}
        self.config.write_text(json.dumps(value))
        self.cfg = p.load_config(self.config)
        self.exif_stub = patch('media_metadata.read_photo_tags', return_value={'status': 'unit-probe-fixture', 'tags': {}})
        self.exif_stub.start()
    def tearDown(self):
        self.exif_stub.stop()
        self.tmp.cleanup()
    def fixtures(self, n=32, capture='2029-10-24'):
        library = pathlib.Path(self.cfg['library_root'])
        for i in range(n):
            name = f'中文 空格 {i:02d}.jpg'
            (library / name).write_bytes(('synthetic-' + str(i)).encode())
            if capture:
                (library / (name + '.mytv.json')).write_text(json.dumps({'schema': 1, 'capture_date': capture}))
        with patch.object(p, 'probe', return_value={'streams': [{'codec_type': 'video', 'width': 100, 'height': 200}]}):
            return p.index_library(self.cfg)
    def test_index_originals_unchanged_incremental_move_duplicate(self):
        index = self.fixtures()
        library = pathlib.Path(self.cfg['library_root'])
        before = {x.name: x.read_bytes() for x in library.iterdir()}
        with patch.object(p, 'probe') as probe:
            again = p.index_library(self.cfg)
        self.assertEqual(again['incremental'], {'hashed': 0, 'reused': 32}); probe.assert_not_called()
        self.assertEqual(before, {x.name: x.read_bytes() for x in library.iterdir()})
        original = next(x for x in library.iterdir() if x.suffix == '.jpg')
        original.rename(library / 'moved.jpg')
        (library / 'duplicate.jpg').write_bytes((library / 'moved.jpg').read_bytes())
        for n in ['moved.jpg', 'duplicate.jpg']:
            (library / (n + '.mytv.json')).write_text('{"schema":1,"capture_date":"2029-10-24"}')
        with patch.object(p, 'probe', return_value={'streams': []}):
            moved = p.index_library(self.cfg)
        plan = p.make_plan(self.cfg, '2030-10-24', moved)
        self.assertEqual(len({x['sha256'] for x in plan['sources']}), len(plan['sources']))
        self.assertEqual(len(moved['records']), 33)
    def test_no_date_never_uses_mtime(self):
        index = self.fixtures(32, None)
        self.assertTrue(all(r['capture_date'] is None and r['time_source'] == 'unknown' for r in index['records']))
        with self.assertRaisesRegex(ValueError, 'Insufficient'): p.make_plan(self.cfg, '2030-10-24', index)
    def test_index_commit_interruption_keeps_old_snapshot_and_resumes(self):
        self.fixtures(1)
        index_path=pathlib.Path(self.cfg['state_root'])/'index.json';old=index_path.read_bytes()
        media=next(pathlib.Path(self.cfg['library_root']).glob('*.jpg'));media.write_bytes(b'new synthetic fixture')
        with patch.object(p,'probe',return_value={'streams':[]}),patch.object(p.os,'replace',side_effect=InterruptedError('index commit interrupted')):
            with self.assertRaises(InterruptedError):p.index_library(self.cfg)
        self.assertEqual(old,index_path.read_bytes())
        with patch.object(p,'probe',return_value={'streams':[]}):new=p.index_library(self.cfg)
        self.assertEqual(new['incremental']['hashed'],1);self.assertEqual(new['records'][0]['sha256'],p.sha(media))
    def test_unknown_timezone_and_naive_time(self):
        side = self.root / 'side.json'
        side.write_text('{"schema":1,"capture_time":"2029-10-24T10:00:00","timezone":"UTC"}')
        with patch.object(p, 'probe', return_value={}):
            self.assertIsNone(p.metadata(self.root / 'x', 'photo', 'UTC', side)['capture_date'])
            side.write_text('{"schema":1,"capture_date":"2029-10-24","timezone":"Not/AZone"}')
            with self.assertRaises(KeyError): p.metadata(self.root / 'x', 'photo', 'UTC', side)
    def test_timezone_cross_day_and_confirmed_tags_only(self):
        side = self.root / 'side.json'
        side.write_text(json.dumps({'schema': 1, 'capture_time': '2029-10-24T23:30:00-02:00', 'location': {'label': 'synthetic', 'confirmed': False},
                                   'people': [{'name': 'confirmed synthetic', 'confirmed': True}, {'name': 'unknown', 'confirmed': False}]}))
        with patch.object(p, 'probe', return_value={}): value = p.metadata(self.root / 'x', 'photo', 'UTC', side)
        self.assertEqual(value['capture_date'], '2029-10-25'); self.assertEqual(len(value['people']), 1); self.assertIsNone(value['location'])
    def test_empty_and_short_libraries_fail_finitely(self):
        index = self.fixtures(0)
        with self.assertRaisesRegex(ValueError, 'Insufficient'): p.make_plan(self.cfg, '2030-10-24', index)
        index = self.fixtures(1)
        with self.assertRaisesRegex(ValueError, 'Insufficient'): p.make_plan(self.cfg, '2030-10-24', index)
    def test_plan_deterministic_same_day_fallback_and_history(self):
        index = self.fixtures()
        first = p.make_plan(self.cfg, '2030-10-24', index)
        self.assertEqual(first, p.make_plan(self.cfg, '2030-10-24', index))
        self.assertTrue(all(x['reason'] == 'historical-same-day' for x in first['explanations']))
        p.note_history(self.cfg, first, 'published')
        events = p.history(self.cfg)['events']; self.assertEqual(events[0]['event'], 'published'); self.assertNotIn('played', events[0].values())
        with self.assertRaisesRegex(ValueError, 'Insufficient'): p.make_plan(self.cfg, '2030-10-25', index)
        self.cfg['selection']['recent_days'] = 0
        fallback = p.make_plan(self.cfg, '2030-10-25', index)
        self.assertTrue(all(x['reason'] == 'same-month-fallback' for x in fallback['explanations']))
    def test_leap_day_policy_explicit_and_missing_year_safe(self):
        index = self.fixtures(capture='2027-02-28')
        self.cfg['selection']['allow_same_month_fallback'] = False
        with self.assertRaisesRegex(ValueError, 'Insufficient'): p.make_plan(self.cfg, '2028-02-29', index)
        self.cfg['selection']['leap_day_fallback'] = 'feb28'
        plan = p.make_plan(self.cfg, '2028-02-29', index)
        self.assertTrue(all(x['reason'] == 'explicit-leap-day-feb28' for x in plan['explanations']))
    def test_stage_copy_hash_and_same_stat_tamper_rejected(self):
        index = self.fixtures(); plan = p.make_plan(self.cfg, '2030-10-24', index); root = p.stage(self.cfg, plan)
        self.assertNotEqual((root / 'inputs').resolve(), pathlib.Path(self.cfg['library_root']))
        self.assertEqual(len(list((root / 'inputs').iterdir())), len(plan['selection']))
        original = pathlib.Path(self.cfg['library_root']) / plan['sources'][0]['file']; before = original.stat()
        original.write_bytes(b'x' * before.st_size); os.utime(original, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'changed'): p.stage(self.cfg, plan)
    def test_disjoint_and_symlink_guard(self):
        cfg = json.loads(self.config.read_text()); cfg['work_root'] = cfg['library_root'] + '/work'; self.config.write_text(json.dumps(cfg))
        with self.assertRaisesRegex(ValueError, 'disjoint'): p.load_config(self.config)
        (self.root / 'link').symlink_to(self.root / 'library')
        with self.assertRaisesRegex(ValueError, 'Symlink'): p.private_dir(self.root / 'link')
    def test_lock_excludes_concurrent_owner(self):
        with p.lock(self.cfg['state_root']):
            with self.assertRaises(BlockingIOError):
                with p.lock(self.cfg['state_root']): pass
    def edition(self, identity='a'):
        root = self.root / ('media-' + identity); root.mkdir()
        movie = root / 'video'; cover = root / 'cover'; movie.write_bytes(('synthetic movie ' + identity).encode()); cover.write_bytes(b'synthetic cover')
        plan = {'programme_id': identity * 64, 'day': '2030-10-24', 'music': None}
        return plan, movie, cover
    def test_publish_and_fetch_corrupt_b_preserve_a(self):
        a, movie, cover = self.edition('a'); pointer = p.publish(self.cfg, a, movie, cover)
        player = self.root / 'player'; p.fetch(self.cfg['publish_root'], player)
        old = (player / 'current.json').read_bytes()
        b, movie, cover = self.edition('b'); p.publish(self.cfg, b, movie, cover)
        current = pathlib.Path(self.cfg['publish_root']) / 'editions' / ('b' * 64)
        (current / 'programme.mp4').write_bytes(b'bad')
        with self.assertRaises(ValueError): p.fetch(self.cfg['publish_root'], player)
        self.assertEqual(old, (player / 'current.json').read_bytes())
        (current / 'programme.mp4').unlink()
        with self.assertRaises(ValueError): p.fetch(self.cfg['publish_root'], player)
        self.assertEqual(old, (player / 'current.json').read_bytes())
    def test_publication_interrupt_preserves_old_pointer(self):
        a, movie, cover = self.edition('a'); p.publish(self.cfg, a, movie, cover)
        pointer = pathlib.Path(self.cfg['publish_root']) / 'current.json'; old = pointer.read_bytes()
        b, movie, cover = self.edition('b')
        original = p.atomic_json
        def fail(path, value):
            if pathlib.Path(path).name == 'current.json': raise InterruptedError('synthetic interruption')
            return original(path, value)
        with patch.object(p, 'atomic_json', side_effect=fail):
            with self.assertRaises(InterruptedError): p.publish(self.cfg, b, movie, cover)
        self.assertEqual(old, pointer.read_bytes())
        p.publish(self.cfg, b, movie, cover)
        self.assertNotEqual(old, pointer.read_bytes())
    def test_bad_manifest_traversal_preserves_player(self):
        a, movie, cover = self.edition('a'); p.publish(self.cfg, a, movie, cover); player = self.root / 'player'; p.fetch(self.cfg['publish_root'], player)
        old = (player / 'current.json').read_bytes(); edition = pathlib.Path(self.cfg['publish_root']) / 'editions' / a['programme_id']
        manifest = p.read_json(edition / 'manifest.json'); manifest['files'][0]['name'] = '../escape'
        p.atomic_json(edition / 'manifest.json', manifest); pointer = p.read_json(pathlib.Path(self.cfg['publish_root']) / 'current.json'); pointer['manifest_sha256'] = p.sha(edition / 'manifest.json'); p.atomic_json(pathlib.Path(self.cfg['publish_root']) / 'current.json', pointer)
        with self.assertRaisesRegex(ValueError, 'allowlist'): p.fetch(self.cfg['publish_root'], player)
        self.assertEqual(old, (player / 'current.json').read_bytes())
    def test_corrupt_media_index_excluded_and_no_original_change(self):
        self.fixtures(1); file = next(pathlib.Path(self.cfg['library_root']).glob('*.jpg')); original = file.read_bytes()
        file.write_bytes(b'invalid fixture changed outside index')
        with patch.object(p, 'probe', side_effect=ValueError('bad media')): index = p.index_library(self.cfg)
        self.assertEqual(index['records'], []); self.assertEqual(len(index['errors']), 1)
        self.assertEqual(file.read_bytes(), b'invalid fixture changed outside index')
    def test_music_requires_license_and_real_file(self):
        cfg = json.loads(self.config.read_text()); cfg['music'] = {'file': str(self.root / 'missing'), 'license_confirmed': False}; self.config.write_text(json.dumps(cfg))
        with self.assertRaisesRegex(ValueError, 'right'): p.load_config(self.config)
        cfg['music']['license_confirmed'] = True; self.config.write_text(json.dumps(cfg))
        with self.assertRaisesRegex(ValueError, 'missing'): p.load_config(self.config)
    def test_confirmed_person_place_preference_ranks_within_date_priority(self):
        index = self.fixtures()
        chosen = index['records'][-1]
        chosen['location'] = 'synthetic place'; chosen['people'] = ['synthetic tag']
        self.cfg['selection']['preferred_locations'] = ['synthetic place']
        self.cfg['selection']['preferred_people'] = ['synthetic tag']
        plan = p.make_plan(self.cfg, '2030-10-24', index)
        self.assertEqual(plan['sources'][0]['sha256'], chosen['sha256']); self.assertEqual(plan['explanations'][0]['preference_matches'], 2)
    def test_readonly_font_symlink_resolves_without_weakening_output_guard(self):
        link=self.root/'system-font'; link.symlink_to(self.root/'font.otf')
        cfg=json.loads(self.config.read_text());cfg['font_file']=str(link);self.config.write_text(json.dumps(cfg))
        self.assertEqual(p.load_config(self.config)['font_file'],str(self.root/'font.otf'))
    def test_old_edition_never_rolls_back_publisher_or_player(self):
        a,movie,cover=self.edition('a');p.publish(self.cfg,a,movie,cover);player=self.root/'player';p.fetch(self.cfg['publish_root'],player)
        b,movie,cover=self.edition('b');b['day']='2030-10-25';p.publish(self.cfg,b,movie,cover);p.fetch(self.cfg['publish_root'],player)
        current=pathlib.Path(self.cfg['publish_root'])/'current.json';before=current.read_bytes();old_player=(player/'current.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'rollback'):p.publish(self.cfg,a,movie,cover)
        self.assertEqual(before,current.read_bytes())
        # A separately received older publication also cannot silently roll back the player.
        other={**self.cfg,'publish_root':str(self.root/'other-publication')};p.publish(other,a,self.root/'media-a/video',self.root/'media-a/cover')
        with self.assertRaisesRegex(ValueError,'rollback'):p.fetch(other['publish_root'],player)
        self.assertEqual(old_player,(player/'current.json').read_bytes())
    def test_edition_fsync_failure_never_advances_pointer(self):
        a,movie,cover=self.edition('a');p.publish(self.cfg,a,movie,cover);player=self.root/'player';p.fetch(self.cfg['publish_root'],player)
        current=pathlib.Path(self.cfg['publish_root'])/'current.json';old=current.read_bytes();b,movie,cover=self.edition('b')
        with patch.object(p,'sync_dir',side_effect=OSError('synthetic fsync failure')):
            with self.assertRaises(OSError):p.publish(self.cfg,b,movie,cover)
        self.assertEqual(old,current.read_bytes())
        p.publish(self.cfg,b,movie,cover);old_player=(player/'current.json').read_bytes()
        with patch.object(p,'sync_dir',side_effect=OSError('synthetic fsync failure')):
            with self.assertRaises(OSError):p.fetch(self.cfg['publish_root'],player)
        self.assertEqual(old_player,(player/'current.json').read_bytes())
    def test_movie_rename_without_receipt_revalidated_before_recovery(self):
        root=p.private_dir(self.root/'reconcile');raw=root/'raw.mp4';movie=root/'programme.mp4';raw.write_bytes(b'raw fixture');movie.write_bytes(b'finished fixture')
        receipt={'sha256':p.sha(raw),'duration':180}
        with patch.object(p,'probe',return_value={'format':{'duration':'180'},'streams':[{'codec_type':'video'}]}),patch.object(p,'strict_decode') as decode:
            result=p.reconcile_movie(root,movie,raw,receipt);decode.assert_called_once();self.assertTrue(result['reconciled_after_interruption'])
        movie.write_bytes(b'tamper')
        with self.assertRaisesRegex(ValueError,'mismatch'):p.reconcile_movie(root,movie,raw,receipt)
    def test_work_recovery_of_old_day_never_reactivates_it(self):
        self.fixtures()
        def fake_renderer(args,log,timeout=900):
            config=p.read_json(args[args.index('--config')+1]);day=args[args.index('--date')+1];output=p.private_dir(pathlib.Path(config['output_root'])/day)
            movie=output/'daily-render.mp4';movie.write_bytes(b'mocked media; not full render acceptance');(output/'daily-render-thumb.jpg').write_bytes(b'cover')
            p.atomic_json(output/'render-receipt.json',{'duration':180,'sha256':p.sha(movie),'full_decode_passed':True,'static_photo_validation':{'passed':True}})
            p.atomic_json(output/'daily-render.presentation.json',{'cover_decode_passed':True});p.atomic_json(output/'adapter-receipt.json',{'mock':True})
        with patch.object(p,'child',side_effect=fake_renderer),patch.object(p,'strict_decode'):
            p.work(self.cfg,'2030-10-24')
        b,movie,cover=self.edition('b');b['day']='2030-10-25';p.publish(self.cfg,b,movie,cover)
        pointer=pathlib.Path(self.cfg['publish_root'])/'current.json';old=pointer.read_bytes()
        with patch.object(p,'child') as render:
            with self.assertRaisesRegex(ValueError,'rollback'):p.work(self.cfg,'2030-10-24')
            render.assert_not_called()
        self.assertEqual(old,pointer.read_bytes())
    def test_actual_timeout_stops_owned_tree_not_unrelated_process(self):
        self.cfg['limits']['timeout_seconds'] = .2
        self.cfg['limits']['term_grace_seconds'] = 1
        actual = subprocess.Popen
        unrelated = actual([sys.executable, '-c', 'import time;time.sleep(10)'])
        code = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],start_new_session=True);time.sleep(10)"
        def launch(_args, **kwargs): return actual([sys.executable, '-c', code], **kwargs)
        try:
            with patch.object(p, 'resource_sample', return_value=(10 * p.GIB, 0, {'memory': 0, 'io': 0})), patch.object(p.shutil, 'which', return_value='/existing'), patch.object(p.subprocess, 'Popen', side_effect=launch):
                with self.assertRaisesRegex(ValueError, 'stopped'): p.supervised(self.config, self.cfg, '2030-10-24')
            receipt = p.read_json(pathlib.Path(self.cfg['state_root']) / 'resource-2030-10-24.json')
            self.assertEqual(receipt['stop_reason'], 'whole_tree_timeout'); self.assertIsNone(unrelated.poll())
            # A killed orphan can be a short-lived zombie until init reaps it; no live workload survives.
            alive = []
            for pid in receipt['surviving_owned_pids']:
                proc = pathlib.Path('/proc') / str(pid) / 'stat'
                if proc.exists() and proc.read_text().split(') ')[1][0] != 'Z': alive.append(pid)
            self.assertEqual(alive, [])
        finally:
            unrelated.terminate(); unrelated.wait(timeout=3)
    def test_attempt_history_keeps_first_receipt_on_repeat(self):
        plan,movie,cover=self.edition('a');p.publish(self.cfg,plan,movie,cover)
        actual=subprocess.Popen
        def launch(_args,**kwargs):return actual([sys.executable,'-c','import time;time.sleep(.05)'],**kwargs)
        with patch.object(p,'resource_sample',return_value=(10*p.GIB,0,{'memory':0,'io':0})),patch.object(p.shutil,'which',return_value='/existing'),patch.object(p.subprocess,'Popen',side_effect=launch):
            p.supervised(self.config,self.cfg,'2030-10-24');receipts=list((pathlib.Path(self.cfg['state_root'])/'attempts/2030-10-24').glob('*/resource.json'));self.assertEqual(len(receipts),1);first=receipts[0];raw=first.read_bytes()
            p.supervised(self.config,self.cfg,'2030-10-24')
        self.assertEqual(first.read_bytes(),raw);self.assertEqual(len(list(first.parent.parent.glob('*/resource.json'))),2)
        with self.assertRaises(FileExistsError):p.immutable_json(first,{'overwrite':True})

if __name__ == '__main__': unittest.main()
