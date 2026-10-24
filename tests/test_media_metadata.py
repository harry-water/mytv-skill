"""Real generated JPEG/video validation; optional embedded EXIF requires existing ExifTool."""
import json, os, pathlib, shutil, subprocess, sys, tempfile, unittest
import ast,hashlib
from unittest.mock import patch
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pipeline as p
import media_metadata as mm

class Provenance(unittest.TestCase):
    def test_local_exif_stays_unknown_and_digitization_not_capture(self):
        tags={'ExifIFD:DateTimeOriginal':'2028:02:29 23:30:00','ExifIFD:CreateDate':'2029:01:01 12:00:00'}
        with patch.object(mm,'read_photo_tags',return_value={'status':'fixture','tags':tags}):
            value=mm.resolve(pathlib.Path('fixture.jpg'),'photo','UTC',{}, {})
        self.assertIsNone(value['capture_date']);self.assertEqual(value['time_candidates'][1]['status'],'local-time-without-offset')
        self.assertTrue(value['time_candidates'][0]['not_assumed_capture_time'])
    def test_exif_offset_sidecar_conflict_and_gps_priority(self):
        tags={'ExifIFD:DateTimeOriginal':'2028:02:29 23:30:00','ExifIFD:OffsetTimeOriginal':'-02:00',
              'GPS:GPSLatitude':12.5,'GPS:GPSLongitude':45.25,'GPS:GPSLatitudeRef':'S','GPS:GPSLongitudeRef':'W'}
        with patch.object(mm,'read_photo_tags',return_value={'status':'fixture','tags':tags}):
            value=mm.resolve(pathlib.Path('fixture.jpg'),'photo','UTC',{}, {})
            self.assertEqual(value['capture_date'],'2028-03-01');self.assertEqual(value['gps']['latitude'],-12.5)
            other=mm.resolve(pathlib.Path('fixture.jpg'),'photo','UTC',{'capture_date':'2027-01-01','gps':{'latitude':0,'longitude':0,'confirmed':True}}, {})
        self.assertEqual(other['capture_date'],'2027-01-01');self.assertTrue(other['time_conflicts']);self.assertTrue(other['gps_conflicts'])
    def test_video_time_conflict_and_policy(self):
        params={'format':{'tags':{'creation_time':'2028-03-01T01:00:00Z'}},'streams':[{'tags':{'creation_time':'2028-03-02T01:00:00Z'}}]}
        value=mm.resolve(pathlib.Path('fixture.mp4'),'video','UTC',{},params,'use-offset')
        self.assertIsNone(value['capture_date']);self.assertTrue(value['time_conflicts'])
        params['streams']=[]
        value=mm.resolve(pathlib.Path('fixture.mp4'),'video','UTC',{},params)
        self.assertEqual(value['time_confidence'],'embedded-unconfirmed')
        value=mm.resolve(pathlib.Path('fixture.mp4'),'video','UTC',{},params,'use-offset')
        self.assertEqual(value['time_confidence'],'embedded-video-offset-policy')
    def test_sidecar_zone_offset_disagreement_blocks_date(self):
        value=mm.resolve(pathlib.Path('fixture.mp4'),'video','UTC',{'capture_time':'2028-03-01T10:00:00+08:00','timezone':'UTC'}, {})
        self.assertIsNone(value['capture_date']);self.assertTrue(value['time_conflicts'])

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Existing FFmpeg required; never installed')
class RealMedia(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mytv-real-metadata-');self.root=pathlib.Path(self.temp.name);self.library=self.root/'library';self.library.mkdir()
        self.photo=self.library/'合成 空格.jpg';self.video=self.library/'合成 视频.mp4'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-threads','1','-f','lavfi','-i','color=c=blue:size=64x48','-frames:v','1','-c:v','mjpeg','-threads','1',str(self.photo)],check=True,capture_output=True,timeout=10)
        subprocess.run(['ffmpeg','-nostdin','-v','error','-threads','1','-f','lavfi','-i','testsrc2=size=64x48:rate=2','-t','12','-c:v','libx264','-preset','ultrafast','-threads','1','-metadata','creation_time=2028-03-01T01:30:00Z','-movflags','+faststart',str(self.video)],check=True,capture_output=True,timeout=10)
        font=self.root/'font';font.write_bytes(b'font fixture; not used by indexing')
        self.file=self.root/'config.json';self.file.write_text(json.dumps({'schema':1,'timezone':'UTC','library_root':str(self.library),'state_root':str(self.root/'state'),'work_root':str(self.root/'work'),'publish_root':str(self.root/'publish'),'font_file':str(font),'limits':{'minimum_free_bytes':1024**2}}));self.cfg=p.load_config(self.file)
    def tearDown(self):self.temp.cleanup()
    def test_actual_bad_image_and_truncated_video_excluded(self):
        bad=self.library/'坏图片.jpg';bad.write_bytes(b'synthetic invalid media fixture');cut=self.library/'截断 视频.mp4';data=self.video.read_bytes();cut.write_bytes(data[:len(data)//2])
        for name in [bad.name,cut.name]: (self.library/(name+'.mytv.json')).write_text('{"schema":1,"capture_date":"2028-03-01"}')
        before={x.name:p.sha(x) for x in self.library.iterdir()};index=p.index_library(self.cfg)
        self.assertEqual({x['file'] for x in index['errors']},{bad.name,cut.name})
        self.assertEqual({x['file'] for x in index['records']},{self.photo.name,self.video.name})
        self.assertEqual(before,{x.name:p.sha(x) for x in self.library.iterdir()})
    def test_actual_container_time_requires_explicit_policy(self):
        index=p.index_library(self.cfg);video=next(x for x in index['records'] if x['kind']=='video')
        self.assertEqual(video['capture_date'],'2028-03-01');self.assertEqual(video['time_confidence'],'embedded-unconfirmed')
        self.cfg['metadata']['video_creation_time_policy']='use-offset';changed=p.index_library(self.cfg);video=next(x for x in changed['records'] if x['kind']=='video')
        self.assertEqual(video['time_confidence'],'embedded-video-offset-policy');self.assertEqual(changed['incremental']['reused'],0)
    @unittest.skipUnless(shutil.which('exiftool'),'Existing optional ExifTool unavailable; not installed')
    def test_actual_exif_original_offset_gps_and_sidecar_conflict(self):
        subprocess.run(['exiftool','-config','','-overwrite_original','-DateTimeOriginal=2028:02:29 23:30:00','-OffsetTimeOriginal=-02:00',
                        '-GPSLatitude=12.5','-GPSLatitudeRef=S','-GPSLongitude=45.25','-GPSLongitudeRef=W',str(self.photo)],check=True,capture_output=True,timeout=15)
        before=p.sha(self.photo);index=p.index_library(self.cfg);photo=next(x for x in index['records'] if x['kind']=='photo')
        self.assertEqual(photo['capture_date'],'2028-03-01');self.assertEqual(photo['time_confidence'],'embedded-offset-original')
        self.assertAlmostEqual(photo['gps']['latitude'],-12.5);self.assertAlmostEqual(photo['gps']['longitude'],-45.25);self.assertEqual(p.sha(self.photo),before)
        side=self.library/(self.photo.name+'.mytv.json');side.write_text('{"schema":1,"capture_date":"2027-01-01"}')
        index=p.index_library(self.cfg);photo=next(x for x in index['records'] if x['kind']=='photo')
        self.assertEqual(photo['capture_date'],'2027-01-01');self.assertTrue(photo['time_conflicts']);self.assertEqual(p.sha(self.photo),before)
    @unittest.skipUnless(shutil.which('exiftool'),'Existing optional ExifTool unavailable; not installed')
    def test_actual_exif_without_offset_remains_unknown(self):
        subprocess.run(['exiftool','-config','','-overwrite_original','-DateTimeOriginal=2028:02:29 23:30:00',str(self.photo)],check=True,capture_output=True,timeout=15)
        photo=next(x for x in p.index_library(self.cfg)['records'] if x['kind']=='photo');self.assertIsNone(photo['capture_date'])
    @unittest.skipUnless(shutil.which('exiftool') and shutil.which('magick') and shutil.which('fc-match'),'Existing image metadata tools required; not installed')
    def test_actual_derived_canvas_removes_source_exif_gps(self):
        subprocess.run(['exiftool','-config','','-overwrite_original','-DateTimeOriginal=2028:02:29 23:30:00','-OffsetTimeOriginal=-02:00','-GPSLatitude=12.5','-GPSLatitudeRef=S','-GPSLongitude=45.25','-GPSLongitudeRef=W',str(self.photo)],check=True,capture_output=True,timeout=15)
        original=p.sha(self.photo);work=self.root/'canvas';work.mkdir()
        for n in ['assets','logs']:(work/n).mkdir()
        font=subprocess.check_output(['fc-match','-f','%{file}\n','Noto Sans CJK SC:style=Bold'],text=True,timeout=10).splitlines()[0]
        sys.path.insert(0,str(ROOT/'runtime/daily-on-this-day'));from encoding import command_for_backend
        source=ROOT/'runtime/daily-on-this-day/render-template.py';tree=ast.parse(source.read_text());defs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ['run','photo']]
        namespace={'Path':pathlib.Path,'subprocess':subprocess,'command_for_backend':command_for_backend,'CTX':{'encoding_backend':'cpu'},'W':work,'FONT':font,'SIZES':{'caption':42,'category':36}}
        exec(compile(ast.Module(body=defs,type_ignores=[]),str(source),'exec'),namespace)
        canvas=namespace['photo']({'source_path':str(self.photo),'input_sha256':original,'caption':'Synthetic date','category':'Synthetic'})
        tags=json.loads(subprocess.check_output(['exiftool','-config','','-j','-GPSLatitude','-GPSLongitude','-DateTimeOriginal',str(canvas)],text=True,timeout=10))[0]
        self.assertFalse(any(k in tags for k in ['GPSLatitude','GPSLongitude','DateTimeOriginal']));self.assertEqual(p.sha(self.photo),original)

if __name__=='__main__':unittest.main()
