"""Real, tiny, offline recoverable-corruption regression for the render decode gate."""
import ast,pathlib,shutil,subprocess,tempfile,unittest,re
ROOT=pathlib.Path(__file__).resolve().parents[1]
class StrictDecode(unittest.TestCase):
 @unittest.skipUnless(shutil.which('ffmpeg'),'Existing FFmpeg required; never installed by test')
 def test_recoverable_corruption_rejected_by_actual_gate(self):
  with tempfile.TemporaryDirectory(prefix='mytv-decode-') as t:
   p=pathlib.Path(t)/'sample.h264'
   subprocess.run(['ffmpeg','-nostdin','-v','error','-threads','1','-filter_threads','1','-f','lavfi','-i','testsrc2=size=128x96:rate=4','-t','2','-c:v','libx264','-preset','ultrafast','-threads','1','-x264-params','keyint=8:scenecut=0',str(p)],check=True,capture_output=True,timeout=10)
   b=bytearray(p.read_bytes());starts=list(re.finditer(b'\x00\x00(?:\x00)?\x01',b));slices=[]
   for i,m in enumerate(starts):
    end=starts[i+1].start() if i+1<len(starts) else len(b)
    if b[m.end()]&31==1 and end-m.end()>80:slices.append((m.end(),end))
   self.assertTrue(slices,'Test fixture needs a P slice')
   start,end=slices[-1];b[start+1:start+65]=b'\xff'*64;p.write_bytes(b)
   tree=ast.parse((ROOT/'runtime/daily-on-this-day/render-template.py').read_text());gate=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='run' and len(n.args)>1 and isinstance(n.args[1],ast.Constant) and n.args[1].value=='full-decode')
   command=[]
   for n in gate.args[0].elts:
    if isinstance(n,ast.Constant):command.append(n.value)
    elif isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='str' and len(n.args)==1 and isinstance(n.args[0],ast.Name) and n.args[0].id=='tmp':command.append(str(p))
    else:self.fail('Decode gate changed; inspect test mapping')
   lax=subprocess.run([x for x in command if x!='-xerror'],capture_output=True,text=True,timeout=10)
   strict=subprocess.run(command,capture_output=True,text=True,timeout=10)
   self.assertEqual(lax.returncode,0,'Fixture should demonstrate a recoverable decode error')
   self.assertTrue(lax.stderr.strip(),'Fixture must log a decode error')
   self.assertNotEqual(strict.returncode,0,'Actual strict gate must reject recoverable corruption')
if __name__=='__main__':unittest.main()
