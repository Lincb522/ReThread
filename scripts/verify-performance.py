"""Read-only performance verification using the user's actual indexed library.
Private conversation fields live only in a temporary owner-only directory.
The retained result contains timings and counts, not titles, IDs or paths.
"""
import argparse,json,os,subprocess,sys,tempfile
from pathlib import Path
project=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(project))
from manager import IndexedStore
p=argparse.ArgumentParser();p.add_argument('--home',type=Path,default=Path.home()/'.codex');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
out=args.output.expanduser().resolve()
if out.exists():raise SystemExit('Use a new output directory to preserve earlier results.')
out.mkdir(parents=True,mode=0o700)
rows,total=IndexedStore(args.home).list(limit=10000)
if len(rows)<=100:raise SystemExit('This real-library performance check expects over 100 indexed conversations.')
with tempfile.TemporaryDirectory(prefix='rethread-performance-') as temp:
 data=Path(temp)/'library.json';data.write_text(json.dumps({'sessions':rows,'total':total},ensure_ascii=False))
 env=os.environ.copy();env['RETHREAD_PERFORMANCE_LIBRARY']=str(data);env['RETHREAD_PERFORMANCE_RESULT']=str(out/'timings.json')
 with (out/'verification.log').open('w') as log:
  subprocess.run(['swift','test','--package-path',str(project),'--jobs','4','--filter','PerformanceRegression'],env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
print(out/'timings.json')
