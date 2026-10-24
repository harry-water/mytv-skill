#!/usr/bin/env python3
"""Local upstream-selection renderer. No network, scheduler, or device control."""
from pathlib import Path
import argparse, datetime as dt, hashlib, json, os, shutil, subprocess, sys, signal, time, fcntl, stat, math
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
REQUIRED_FILTERS=('zscale','drawtext','xfade','acrossfade','ssim')
RECEIPT_SCHEMA_VERSION=2
def validate_receipt_schema(receipt):
 require(isinstance(receipt,dict) and type(receipt.get('receipt_schema_version')) is int and receipt['receipt_schema_version']==RECEIPT_SCHEMA_VERSION,'Legacy or unsupported receipt schema; preserve output and use a new output_root')
FILTER_SPECIAL="':\\\n\r,;[]"
def validate_filter_path(path):
 require(not any(c in str(path) for c in FILTER_SPECIAL),'Unsupported filter-special character in path')
def require(ok, message):
 if not ok: raise ValueError(message)
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def write(p,value):
 with Path(p).open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
def doctor(font,backend='nvenc'):
 require(backend in ('nvenc','cpu'),'encoding_backend must be nvenc or cpu')
 for name in ('ffmpeg','ffprobe','magick'):
  require(shutil.which(name) is not None,'Missing dependency: '+name)
 require(font.is_file(),'Missing external font')
 # Filter paths are embedded by the legacy renderer; reject unverified syntax.
 validate_filter_path(font)
 enc=subprocess.check_output(['ffmpeg','-hide_banner','-encoders'],text=True,stderr=subprocess.DEVNULL,timeout=15)
 encoder='h264_nvenc' if backend=='nvenc' else 'libx264'
 require(encoder in enc,'Missing encoder: '+encoder)
 filters=subprocess.check_output(['ffmpeg','-hide_banner','-filters'],text=True,stderr=subprocess.DEVNULL,timeout=15)
 for term in REQUIRED_FILTERS:require(term in filters,'Missing FFmpeg filter: '+term)
 subprocess.run(['ffmpeg','-nostdin','-v','error','-threads','1','-f','lavfi','-i','color=size=320x240:rate=30','-frames:v','1','-c:v',encoder,'-f','null','-'],check=True,capture_output=True,timeout=20)
 result={'python':sys.version.split()[0],'backend':backend,'font_sha256':sha(font)}
 for name in ('ffmpeg','ffprobe','magick'):
  result[name]=subprocess.check_output([name,'-version'],text=True,timeout=15).splitlines()[0]
 return result
def no_symlink_chain(path):
 path=Path(os.path.abspath(path))
 for p in [path,*path.parents]:
  require(not p.is_symlink(),'Symlink in output path: '+str(p))
 return path
def output_dir(root,day):
 root=no_symlink_chain(root)
 root.mkdir(parents=True,exist_ok=True,mode=0o700)
 s=root.stat();require(s.st_uid==os.getuid() and not stat.S_IMODE(s.st_mode)&0o077,'Output root must be owned by current user and private (0700)')
 output=root/day.isoformat();no_symlink_chain(output)
 if output.exists():
  require(output.is_dir(),'Output date path is not a directory')
  for p in output.rglob('*'):require(not p.is_symlink(),'Symlink in existing output tree')
 else:output.mkdir(mode=0o700)
 return output
def selection(raw,inputs,day):
 require(isinstance(raw,list) and bool(raw),'Empty album')
 require(len(raw)<=46,'Too many assets')
 require(all(isinstance(r,dict) for r in raw),'Selection entries must be objects')
 require(any(r.get('kind')=='photo' for r in raw),'At least one photo is required for static validation and cover')
 sys.path.insert(0,str(ROOT/'runtime/daily-on-this-day'))
 from video_clip import clip_spec
 rows=[];seen=set()
 for i,r in enumerate(raw):
  require(isinstance(r.get('file'),str) and not Path(r['file']).is_absolute(),'Input must be a relative path')
  p=(inputs/r['file']).resolve();require(p.is_relative_to(inputs) and p.is_file(),'Input outside configured root or missing')
  digest=sha(p);require(digest not in seen,'Duplicate source');seen.add(digest)
  capture=dt.date.fromisoformat(r['capture_date'][:10]);require(capture.year<day.year,'Capture year must precede programme year')
  chapter=r['chapter'];require(chapter in ('same-day','month-memory','season','trip'),'Invalid chapter')
  require(capture.strftime('%m-%d')==day.strftime('%m-%d') if chapter=='same-day' else capture.month==day.month,'Capture date does not match chapter')
  kind=r['kind'];require(kind in ('photo','video'),'Invalid asset kind')
  if kind=='photo':subprocess.run(['magick','identify',str(p)+'[0]'],check=True,capture_output=True,timeout=30);meta={}
  else:
   m=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(p)],text=True,timeout=30));meta={'QuickTime:Duration':float(m['format']['duration'])}
  s=p.stat();rows.append({'id':i+1,'source_id':'configured-local-input','source_path':str(p),'kind':kind,'capture_date':capture.isoformat(),'year':capture.year,'chapter':chapter,'input_sha256':digest,'sha256':digest,'original_stat':[s.st_size,s.st_mtime_ns],'metadata_json':meta,'caption':capture.strftime('%Y.%m.%d'),'category':{'same-day':'同日回忆','month-memory':'本月回忆','season':'季节补景','trip':'行程延伸'}[chapter],'video_start_seconds':r.get('video_start_seconds',0),'video_speed':r.get('video_speed',1)})
  if kind=='video':clip_spec(rows[-1])  # Validate before doctor/encoding/output creation; do not alter the row.
 require(sum(x['chapter']=='season' for x in rows)/len(rows)<=.2,'Too many seasonal supplements')
 trip=[dt.date.fromisoformat(x['capture_date']) for x in rows if x['chapter']=='trip']
 if trip:
  require(len({x.year for x in trip})==1 and any(x.strftime('%m-%d')==day.strftime('%m-%d') for x in trip),'Trip needs one year and same-day anchor')
  anchor=dt.date(trip[0].year,day.month,day.day);require((max(trip)-min(trip)).days<=13 and all(-3<=(x-anchor).days<=13 for x in trip),'Trip date range invalid')
 return rows
def fingerprint(rows,cfg,policy,day,runtime):
 closure={str(p.relative_to(ROOT)):sha(p) for folder in ('scripts','runtime','config') for p in sorted((ROOT/folder).rglob('*')) if p.is_file() and p.suffix in ('.py','.json')}
 value={'receipt_schema_version':RECEIPT_SCHEMA_VERSION,'rows':rows,'config':cfg,'policy':policy,'date':day.isoformat(),'runtime':runtime,'code_closure':closure}
 return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
def execute(cmd,cwd,log):
 with log.open('x') as f:
  detached=os.environ.get('MYTV_PIPELINE_OWNED')!='1'
  proc=subprocess.Popen(cmd,cwd=cwd,stdout=f,stderr=subprocess.STDOUT,start_new_session=detached)
  try:rc=proc.wait()
  except KeyboardInterrupt:
   if detached:os.killpg(proc.pid,signal.SIGTERM)
   else:proc.terminate()
   try:proc.wait(timeout=15)
   except subprocess.TimeoutExpired:
    if detached:os.killpg(proc.pid,signal.SIGKILL)
    else:proc.kill()
    proc.wait()
   raise
 require(rc==0,'Child failed; inspect isolated log: '+str(log))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('command',choices=['doctor','render']);ap.add_argument('--config',required=True);ap.add_argument('--selection');ap.add_argument('--date');a=ap.parse_args()
 cfg=json.loads(Path(a.config).read_text());font=Path(cfg['font_file']).expanduser().resolve();backend=cfg.get('encoding_backend','nvenc');validate_filter_path(font)
 if a.command=='render':validate_filter_path(Path(cfg['output_root']).expanduser().absolute())
 if a.command=='doctor':print(json.dumps(doctor(font,backend)));return
 day=dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(ZoneInfo(cfg['timezone'])).date()
 require(a.selection is not None,'--selection is required')
 raw=json.loads(Path(a.selection).read_text());require(isinstance(raw,list) and bool(raw),'Empty album')
 inputs=Path(cfg['input_root']).expanduser().resolve();outputs=no_symlink_chain(Path(cfg['output_root']).expanduser())
 require(not outputs.is_relative_to(inputs) and not inputs.is_relative_to(outputs),'Input/output trees must be disjoint')
 validate_filter_path(outputs)
 rows=selection(raw,inputs,day);policy=json.loads((ROOT/'config/duration.json').read_text());sys.path.insert(0,str(ROOT/'runtime/daily-on-this-day'))
 from duration_plan import plan
 plan(rows,policy);runtime=doctor(font,backend);fp=fingerprint(rows,cfg,policy,day,runtime);output=output_dir(outputs,day)
 fd=os.open(output/'adapter.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);done=output/'adapter-receipt.json'
  if done.exists():
   j=json.loads(done.read_text());validate_receipt_schema(j);require(j['fingerprint']==fp and sha(output/'daily-render.mp4')==j['film_sha256'] and sha(output/'daily-render-thumb.jpg')==j['thumbnail_sha256'],'Existing output differs; use a new output_root')
   print(json.dumps({'status':'idempotent_reuse','film_sha256':j['film_sha256']}));return
  require(set(p.name for p in output.iterdir())=={'adapter.lock'},'Partial/existing output preserved; use a new output_root')
  write(output/'selection.json',rows);style={'version':'mytv-v1.0.0-neutral','font_bold':str(font),'font_sha256':sha(font),'font_weight':700,'font_sizes':{'title':112,'subtitle':48,'caption':42,'category':36,'ai_label':26},'themes_by_month':{}}
  write(output/'context.json',{'month':day.month,'day':day.day,'date':day.isoformat(),'opening_sub':f'{day.month}月{day.day}日 · 本地回忆影片','same_day_title':f'那些年的{day.month}月{day.day}日','duration_policy':policy,'presentation':style,'encoding_backend':backend})
  shutil.copy2(ROOT/'runtime/daily-on-this-day/render-template.py',output/'render.py');os.environ['MYTV_RUNTIME_ROOT']=str(ROOT/'runtime');begin=time.monotonic();execute([sys.executable,str(output/'render.py')],output,output/'adapter-render.log')
  rr=json.loads((output/'render-receipt.json').read_text());prepared=json.loads((output/'prepared-final-selection.json').read_text());cover=next(x['asset'] for x in prepared if x['kind']=='photo')
  execute([sys.executable,str(ROOT/'runtime/prepare_video_presentation.py'),'--video',str(output/'daily-render.mp4'),'--title','历史上的今天 · '+day.isoformat(),'--cover-source',cover,'--expected-video-sha256',rr['sha256']],output,output/'adapter-cover.log')
  write(done,{'receipt_schema_version':RECEIPT_SCHEMA_VERSION,'status':'rendered_and_covered','fingerprint':fp,'film_sha256':sha(output/'daily-render.mp4'),'thumbnail_sha256':sha(output/'daily-render-thumb.jpg'),'duration_seconds':rr['duration'],'photos':rr['photos'],'video_clips':rr['video_clips'],'elapsed_render_and_cover_seconds':round(time.monotonic()-begin,3),'runtime':runtime,'television_tested':False,'network_used':False});print(done.read_text())
if __name__=='__main__':main()
