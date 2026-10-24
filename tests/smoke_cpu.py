#!/usr/bin/env python3
"""Small real CPU encoder and cover check, not a full programme acceptance."""
import argparse,pathlib,sys,subprocess,json,hashlib,time
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime/daily-on-this-day'))
from encoding import command_for_backend
p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--font',required=True);a=p.parse_args();out=pathlib.Path(a.output).resolve();out.mkdir(mode=0o700,parents=True,exist_ok=False);font=pathlib.Path(a.font).resolve()
if any(c in str(font) for c in "':\\\n\r"):raise ValueError('Unsupported font path')
text=out/'caption.txt';text.write_text('MyTV 合成测试');video=out/'sample.mp4';cover=out/'source.jpg';begin=time.monotonic()
cmd=['ffmpeg','-nostdin','-v','error','-threads','1','-filter_threads','1','-f','lavfi','-i','testsrc2=size=1280x720:rate=2','-vf',f'drawtext=fontfile={font}:textfile={text}:fontsize=42:fontcolor=white:x=40:y=40,format=yuv420p','-t','2','-an','-c:v','h264_nvenc','-preset','p4','-rc','vbr','-cq','18','-b:v','0','-threads','1',str(video)]
subprocess.run(command_for_backend(cmd,'cpu'),check=True,capture_output=True,timeout=20)
subprocess.run(['ffmpeg','-nostdin','-v','error','-threads','1','-filter_threads','1','-i',str(video),'-frames:v','1','-threads','1',str(cover)],check=True,capture_output=True,timeout=15)
original=hashlib.sha256(video.read_bytes()).hexdigest()
r=subprocess.run([sys.executable,str(ROOT/'runtime/prepare_video_presentation.py'),'--video',str(video),'--title','MyTV 合成测试','--cover-source',str(cover),'--expected-video-sha256',original],check=True,capture_output=True,text=True,timeout=30);j=json.loads(r.stdout)
if hashlib.sha256(video.read_bytes()).hexdigest()!=original:raise ValueError('Original video changed')
bad=out/'broken.mp4';bad.write_bytes(b'not a video');negative=subprocess.run([sys.executable,str(ROOT/'runtime/prepare_video_presentation.py'),'--video',str(bad),'--title','bad'],capture_output=True,text=True,timeout=15)
if negative.returncode==0:raise ValueError('Corrupt media accepted')
repeat=subprocess.run([sys.executable,str(ROOT/'runtime/prepare_video_presentation.py'),'--video',str(video),'--title','MyTV 合成测试','--cover-source',str(cover),'--expected-video-sha256',original],check=True,capture_output=True,text=True,timeout=15)
if json.loads(repeat.stdout)['cover_sha256']!=j['cover_sha256']:raise ValueError('Repeat cover changed')
result={'scope':'2 seconds, 4 frames, 1280x720 real libx264 encoding and actual cover validation; not full renderer/240-second/TV test','elapsed_seconds':round(time.monotonic()-begin,3),'film_bytes':video.stat().st_size,'film_sha256':original,'cover_sha256':j['cover_sha256'],'cover_dimensions':j['cover_dimensions'],'cover_repeat':'passed','corrupt_video':'rejected','original_unchanged':True,'network_used':False}
(out/'smoke-receipt.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
