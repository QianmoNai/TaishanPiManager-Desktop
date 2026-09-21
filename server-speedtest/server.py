import http.server, socketserver, threading, os, time
from urllib.parse import urlsplit
CHUNK=os.urandom(65536)
class Server(socketserver.ThreadingMixIn,http.server.HTTPServer):
 daemon_threads=True
 slots=threading.BoundedSemaphore(8)
 def process_request(self,request,address):
  if not self.slots.acquire(False):request.close();return
  try:super().process_request(request,address)
  except Exception:self.slots.release();raise
 def process_request_thread(self,request,address):
  try:super().process_request_thread(request,address)
  finally:self.slots.release()
class Handler(http.server.BaseHTTPRequestHandler):
 def setup(self):super().setup();self.connection.settimeout(10)
 def log_message(self,*args):pass
 def reply(self,data,status=200,kind='text/plain'):
  self.send_response(status);self.send_header('Content-Length',str(len(data)));self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store');self.send_header('Connection','close');self.end_headers();self.wfile.write(data)
 def do_GET(self):
  path=urlsplit(self.path).path
  try:
   if path=='/speedtest/latency.txt':self.reply(b'test=test\n')
   elif path=='/speedtest/random4000x4000.jpg':
    self.send_response(200);self.send_header('Content-Length',str(16*1024*1024));self.send_header('Content-Type','image/jpeg');self.send_header('Cache-Control','no-store');self.end_headers()
    for _ in range(256):self.wfile.write(CHUNK)
   else:self.reply(b'Not found',404)
  except (OSError,TimeoutError):pass
 def do_POST(self):
  try:
   if urlsplit(self.path).path!='/speedtest/upload.php':self.reply(b'Not found',404);return
   if self.headers.get('Transfer-Encoding'):self.reply(b'Unsupported',400);return
   length=int(self.headers.get('Content-Length','-1'))
   if not 0<=length<=4*1024*1024:self.reply(b'Too large',413);return
   remaining=length;deadline=time.monotonic()+20
   while remaining:
    if time.monotonic()>deadline:return
    data=self.rfile.read(min(65536,remaining))
    if not data:return
    remaining-=len(data)
   self.reply(('size='+str(length)).encode())
  except (OSError,ValueError):pass
Server(('0.0.0.0',8080),Handler).serve_forever()
