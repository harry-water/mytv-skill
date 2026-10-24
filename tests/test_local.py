"""Bounded offline regression tests; fake renderer tests do not prove video quality."""
import ast,datetime,hashlib,importlib.util,json,os,pathlib,subprocess,sys,tempfile,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('adapter',ROOT/'scripts/run.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class LocalTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='mytv-test-');self.p=pathlib.Path(self.tmp.name);self.inputs=self.p/'中文 输入';self.inputs.mkdir();self.font=self.p/'font.otf';self.font.write_bytes(b'fake font for unit test');self.day=datetime.date(2030,10,24)
 def tearDown(self):self.tmp.cleanup()
 def rows(self,n=32):
  out=[]
  for i in range(n):
   name=f'照片 {i}.jpg';(self.inputs/name).write_bytes(bytes([i]));out.append({'file':name,'kind':'photo','capture_date':'2029-10-24','chapter':'same-day'})
  return out
 def test_empty(self):
  with self.assertRaisesRegex(ValueError,'Empty'):m.selection([],self.inputs,self.day)
 def test_bad_entry(self):
  with self.assertRaisesRegex(ValueError,'objects'):m.selection([None],self.inputs,self.day)
 def test_video_only_early(self):
  with self.assertRaisesRegex(ValueError,'photo'):m.selection([{'kind':'video'}],self.inputs,self.day)
 def test_path_escape(self):
  r=self.rows(1);r[0]['file']='../font.otf'
  with self.assertRaisesRegex(ValueError,'outside'):m.selection(r,self.inputs,self.day)
 def test_absolute_path(self):
  r=self.rows(1);r[0]['file']=str(self.font)
  with self.assertRaisesRegex(ValueError,'relative'):m.selection(r,self.inputs,self.day)
 def test_corrupt_image_dependency_failure(self):
  with patch.object(m.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['magick'])):
   with self.assertRaises(subprocess.CalledProcessError):m.selection(self.rows(1),self.inputs,self.day)
 def test_duplicate(self):
  r=self.rows(2);(self.inputs/r[1]['file']).write_bytes(b'\x00')
  with patch.object(m.subprocess,'run'):
   with self.assertRaisesRegex(ValueError,'Duplicate'):m.selection(r,self.inputs,self.day)
 def test_chinese_spaces_and_original_unchanged(self):
  r=self.rows();before={p:p.read_bytes() for p in self.inputs.iterdir()}
  with patch.object(m.subprocess,'run'):selected=m.selection(r,self.inputs,self.day)
  self.assertEqual(len(selected),32);self.assertEqual(before,{p:p.read_bytes() for p in self.inputs.iterdir()})
 def test_output_symlink(self):
  out=self.p/'link';out.symlink_to(self.inputs,target_is_directory=True)
  with self.assertRaisesRegex(ValueError,'Symlink'):m.output_dir(out,self.day)
 def test_output_child_symlink(self):
  out=m.output_dir(self.p/'out',self.day);(out/'selection.json').symlink_to(self.font)
  with self.assertRaisesRegex(ValueError,'Symlink'):m.output_dir(self.p/'out',self.day)
  self.assertEqual(self.font.read_bytes(),b'fake font for unit test')
 def test_missing_acrossfade(self):
  def result(cmd,**kw):return 'h264_nvenc libx264' if '-encoders' in cmd else 'zscale drawtext xfade ssim'
  with patch.object(m.shutil,'which',return_value='/existing/tool'),patch.object(m.subprocess,'check_output',side_effect=result):
   with self.assertRaisesRegex(ValueError,'acrossfade'):m.doctor(self.font,'cpu')
 def test_fingerprint_font_runtime_and_dependency(self):
  clone=self.p/'closure';(clone/'runtime').mkdir(parents=True);(clone/'scripts').mkdir();(clone/'config').mkdir();dep=clone/'runtime/module.py';dep.write_text('x=1')
  with patch.object(m,'ROOT',clone):
   x=m.fingerprint([],{}, {},self.day,{'font_sha256':'a'});dep.write_text('x=2');y=m.fingerprint([],{}, {},self.day,{'font_sha256':'a'});z=m.fingerprint([],{}, {},self.day,{'font_sha256':'b'})
  self.assertNotEqual(x,y);self.assertNotEqual(y,z)
 def test_repeat_reuses_and_changed_font_rejects_without_overwrite(self):
  selection=self.p/'selection.json';selection.write_text(json.dumps(self.rows()));out=self.p/'独立 输出';cfg={'font_file':str(self.font),'timezone':'UTC','input_root':str(self.inputs),'output_root':str(out),'encoding_backend':'cpu'};config=self.p/'config.json';config.write_text(json.dumps(cfg))
  calls=[]
  def execute(cmd,cwd,log):
   calls.append(cmd);log.write_text('unit fake renderer, not media validation')
   if cmd[1].endswith('render.py'):
    (cwd/'daily-render.mp4').write_bytes(b'unit-film');(cwd/'render-receipt.json').write_text(json.dumps({'sha256':m.sha(cwd/'daily-render.mp4'),'duration':240,'photos':32,'video_clips':0}));(cwd/'prepared-final-selection.json').write_text(json.dumps([{'kind':'photo','asset':str(self.font)}]))
   else:(cwd/'daily-render-thumb.jpg').write_bytes(b'unit-cover')
  argv=['run.py','render','--config',str(config),'--selection',str(selection),'--date',self.day.isoformat()]
  before={p:p.read_bytes() for p in self.inputs.iterdir()}
  with patch.object(sys,'argv',argv),patch.object(m,'doctor',side_effect=lambda font,backend:{'font_sha256':m.sha(font),'backend':backend}),patch.object(m.subprocess,'run'),patch.object(m,'execute',side_effect=execute):
   m.main();context=json.loads((out/self.day.isoformat()/'context.json').read_text());self.assertIn('本地回忆影片',context['opening_sub']);self.assertNotIn('人工',context['opening_sub']);film=out/self.day.isoformat()/'daily-render.mp4';first=film.stat().st_mtime_ns;m.main();self.assertEqual(len(calls),2);self.assertEqual(film.stat().st_mtime_ns,first)
   done=out/self.day.isoformat()/'adapter-receipt.json';original_receipt=done.read_bytes();legacy=json.loads(original_receipt);legacy.pop('receipt_schema_version');done.write_text(json.dumps(legacy));legacy_bytes=done.read_bytes()
   with self.assertRaisesRegex(ValueError,'schema'):m.main()
   self.assertEqual(done.read_bytes(),legacy_bytes);self.assertEqual(film.stat().st_mtime_ns,first);self.assertEqual(len(calls),2);done.write_bytes(original_receipt)
   self.font.write_bytes(b'changed font')
   with self.assertRaisesRegex(ValueError,'differs'):m.main()
   self.assertEqual(film.read_bytes(),b'unit-film')
  self.assertEqual(before,{p:p.read_bytes() for p in self.inputs.iterdir()})
 def test_filter_special_paths_rejected_before_tools(self):
  for char in [',',';','[',']',':',"'",'\\','\n','\r']:
   with self.subTest(character=repr(char)):
    cfg={'font_file':str(self.font),'timezone':'UTC','input_root':str(self.inputs),'output_root':str(self.p/('bad'+char+'out'))}
    config=self.p/'config-bad.json';config.write_text(json.dumps(cfg));selection=self.p/'selection-bad.json';selection.write_text('[]')
    with patch.object(sys,'argv',['run.py','render','--config',str(config),'--selection',str(selection)]),patch.object(m.subprocess,'run') as proc:
     with self.assertRaisesRegex(ValueError,'filter-special'):m.main()
     proc.assert_not_called()
    with self.assertRaisesRegex(ValueError,'filter-special'):m.validate_filter_path(str(self.font)+char)
 def video_rows(self):
  photo=self.rows(1)[0];(self.inputs/'片段 空格.mp4').write_bytes(b'standard-library unit fixture, no decoder acceptance claim')
  return [photo,{'file':'片段 空格.mp4','kind':'video','capture_date':'2029-10-24','chapter':'same-day','video_start_seconds':0,'video_speed':1}]
 def test_invalid_video_speed_before_encoder(self):
  for speed in [0.5,5,float('nan'),float('inf')]:
   rows=self.video_rows();rows[1]['video_speed']=speed
   with self.subTest(speed=speed),patch.object(m.subprocess,'run'),patch.object(m.subprocess,'check_output',return_value='{"format":{"duration":"12"}}'),patch.object(m,'doctor') as doctor,patch.object(m,'execute') as execute:
    with self.assertRaises(ValueError):m.selection(rows,self.inputs,self.day)
    doctor.assert_not_called();execute.assert_not_called()
 def test_video_excerpt_out_of_bounds_before_output(self):
  rows=self.video_rows();rows[1]['video_start_seconds']=3.1
  with patch.object(m.subprocess,'run'),patch.object(m.subprocess,'check_output',return_value='{"format":{"duration":"12"}}'):
   with self.assertRaisesRegex(ValueError,'bounds'):m.selection(rows,self.inputs,self.day)
  self.assertFalse((self.p/'out').exists())
 def test_video_exact_boundary_preserves_row(self):
  rows=self.video_rows();rows[1]['video_start_seconds']=3
  before={p:p.read_bytes() for p in self.inputs.iterdir()}
  with patch.object(m.subprocess,'run'),patch.object(m.subprocess,'check_output',return_value='{"format":{"duration":"12"}}'):
   selected=m.selection(rows,self.inputs,self.day)
  self.assertEqual(selected[1]['video_start_seconds'],3);self.assertEqual(selected[1]['video_speed'],1)
  self.assertEqual(before,{p:p.read_bytes() for p in self.inputs.iterdir()})
 def test_receipt_schema_current_and_unknown(self):
  m.validate_receipt_schema({'receipt_schema_version':2})
  for value in [{},{'receipt_schema_version':1},{'receipt_schema_version':3},{'receipt_schema_version':'2'},{'receipt_schema_version':True},None]:
   with self.subTest(receipt=value),self.assertRaisesRegex(ValueError,'schema'):m.validate_receipt_schema(value)
 def test_optimized_mode_still_rejects(self):
  code="import importlib.util; s=importlib.util.spec_from_file_location('x',"+repr(str(ROOT/'scripts/run.py'))+");m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m.require(False,'rejected')"
  r=subprocess.run([sys.executable,'-O','-c',code],capture_output=True,text=True,timeout=5);self.assertNotEqual(r.returncode,0);self.assertIn('ValueError: rejected',r.stderr)
 def test_receipt_does_not_claim_location_captions(self):
  tree=ast.parse((ROOT/'runtime/daily-on-this-day/render-template.py').read_text())
  receipt=next(n.value for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='receipt' for t in n.targets))
  fields={k.value:v for k,v in zip(receipt.keys,receipt.values) if isinstance(k,ast.Constant)}
  self.assertIs(ast.literal_eval(fields['location_captions']),False)
 def test_upstream_fields_do_not_claim_person_or_location_support(self):
  rows=self.rows(1);rows[0].update(location='synthetic place',people=['synthetic person'])
  with patch.object(m.subprocess,'run'):selected=m.selection(rows,self.inputs,self.day)
  self.assertNotIn('location',selected[0]);self.assertNotIn('people',selected[0]);self.assertEqual(selected[0]['caption'],'2029.10.24')
 def test_no_assert_in_published_sources(self):
  for p in ROOT.rglob('*.py'):
   if 'tests' not in p.parts:self.assertFalse(any(isinstance(n,ast.Assert) for n in ast.walk(ast.parse(p.read_text()))),str(p))
 def test_cpu_command_has_no_nvenc_options(self):
  sys.path.insert(0,str(ROOT/'runtime/daily-on-this-day'))
  from encoding import command_for_backend
  cmd=command_for_backend(['ffmpeg','-c:v','h264_nvenc','-preset','p4','-rc','vbr','-cq','18','-b:v','0','out.mkv'],'cpu')
  self.assertEqual(cmd[-1],'out.mkv');self.assertIn('libx264',cmd);self.assertNotIn('-rc',cmd);self.assertNotIn('-cq',cmd)
if __name__=='__main__':unittest.main()
