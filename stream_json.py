"""Bounded-memory official JSON-RPC response transport using ijson 3.5.1 (BSD)."""
import os,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'vendor'))
import ijson.backends.python as parser


def responses(source,path):
    """Spool newline-framed RPC messages, parse without materializing full history."""
    path=Path(path)
    while True:
        chunk=source.readline(65536)
        if not chunk:return
        fd,name=tempfile.mkstemp(prefix='.rpc-',dir=path.parent)
        temp=Path(name)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as out:
                while True:
                    out.write(chunk)
                    if chunk.endswith('\n'):break
                    chunk=source.readline(65536)
                    if not chunk:raise ValueError('官方响应在 JSON 帧完成前中断')
                out.flush();os.fsync(out.fileno())
            header={};error={}
            with temp.open('rb') as f:
                for prefix,event,value in parser.parse(f):
                    if prefix in {'id','method'} and event in {'string','number'}:header[prefix]=value
                    if prefix in {'error.code','error.message'}:error[prefix.split('.')[1]]=value
            if error:header['error']=error
            elif header.get('id')==2 and 'method' not in header:
                if path.exists():raise ValueError('官方读取证据文件已存在，停止覆盖')
                os.replace(temp,path);header['result']={'stream_path':str(path)}
            elif header.get('id')==1:
                # Only the initialize envelope, not the conversation body.
                with temp.open('rb') as f:
                    header['result']=next(parser.items(f,'result'),None)
            yield header
        finally:
            if temp.exists():temp.unlink()


def visible_items(path):
    with Path(path).open('rb') as f:
        yield from parser.items(f,'result.thread.turns.item.items.item')


def thread_identity(path):
    out={}
    with Path(path).open('rb') as f:
        for prefix,event,value in parser.parse(f):
            if prefix in {'result.thread.id','result.thread.name'} and event in {'string','null'}:
                out[prefix.rsplit('.',1)[1]]=value
    return out
