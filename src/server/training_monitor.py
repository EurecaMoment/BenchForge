"""Live training events and browser dashboard, backed by append-only JSONL."""
import json
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlparse

from storage.training_runs import guide
from storage.training_runs import snapshot


# 启动训练监控 HTTP 服务，提供状态、指导接口及内置监控页面资源。
def serve(run,host='127.0.0.1',port=8767):
    class Handler(BaseHTTPRequestHandler):
        # 仅接受训练指导接口，将 JSON 设置合并到运行指导并返回更新结果。
        def do_POST(self):
            if urlparse(self.path).path!='/api/guidance':self.send_error(404);return
            settings=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            body=json.dumps(guide(run,settings)).encode();self.send_response(200)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        # 返回训练事件状态或固定的内置监控页面资源，其他路径返回未找到。
        def do_GET(self):
            route=urlparse(self.path).path
            if route=='/api/state':body=json.dumps(snapshot(run),ensure_ascii=False).encode();mime='application/json'
            elif route in ('/', '/monitor.js', '/styles.css'):
                names = {'/': ('index.html', 'text/html'), '/monitor.js': ('monitor.js', 'text/javascript'), '/styles.css': ('styles.css', 'text/css')}
                name, mime = names[route]
                body = files('ui').joinpath('training_monitor', name).read_bytes()
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',mime+'; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        # 抑制默认 HTTP 访问日志，避免监控轮询产生重复终端输出。
        def log_message(self,*args):pass
    ThreadingHTTPServer((host,port),Handler).serve_forever()


# 按需保存调用方指导设置，再返回当前训练事件快照。
def monitor(args,directory,config=None):
    if args.get('guidance') is not None:guide(args['run'],args['guidance'])
    return snapshot(args['run'])


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True);parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8767)
    args=parser.parse_args();serve(args.run,args.host,args.port)
