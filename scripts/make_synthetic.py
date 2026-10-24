#!/usr/bin/env python3
"""Create geometric test inputs on request, not family photos; refuses existing directory."""
from pathlib import Path
import argparse,json,subprocess,datetime as dt
p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--font',required=True);p.add_argument('--date',default='2030-10-24');p.add_argument('--backend',choices=['nvenc','cpu'],default='nvenc');a=p.parse_args()
root=Path(a.root).resolve();root.mkdir(parents=True,exist_ok=False,mode=0o700)
inputs=root/'合成 图片';inputs.mkdir();selection=[];day=dt.date.fromisoformat(a.date);capture=day.replace(year=day.year-1).isoformat()
for i in range(28):
 name=f'图形 {i:02d}.jpg';shape='900x1400'if i%3==0 else '1600x900'
 subprocess.run(['magick','-size',shape,f'gradient:#{(20+i*7)%256:02x}7090-#f0d090','-fill','#406040','-draw',f'circle {180+i*10},350 130,550','-font',a.font,'-pointsize','70','-gravity','center','-fill','white','-annotate','0',f'合成测试 {i+1}',str(inputs/name)],check=True)
 selection.append({'file':name,'kind':'photo','capture_date':capture,'chapter':'same-day'})
for i in range(4):
 name=f'动态 {i}.mp4'
 subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=30','-f','lavfi','-i',f'sine=frequency={300+i*100}:sample_rate=48000','-vf',f'hue=h={i*60}','-t','12','-threads','2','-c:v',('h264_nvenc' if a.backend=='nvenc' else 'libx264'),'-c:a','aac',str(inputs/name)],check=True)
 selection.append({'file':name,'kind':'video','capture_date':capture,'chapter':'same-day'})
(root/'selection.json').write_text(json.dumps(selection,ensure_ascii=False,indent=2))
(root/'config.json').write_text(json.dumps({'encoding_backend':a.backend,'font_file':str(Path(a.font).resolve()),'timezone':'Asia/Shanghai','input_root':str(inputs),'output_root':str(root/'独立 输出')},ensure_ascii=False,indent=2))
print(json.dumps({'created':True,'photos':28,'videos':4,'date':a.date,'root':str(root)},ensure_ascii=False))
