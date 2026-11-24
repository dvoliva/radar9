"""Sirve solamente la salida pública en un puerto local con la ruta real de Pages."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .store import ROOT
from .verify import BASE


class Handler(SimpleHTTPRequestHandler):
    def send_head(self):
        path = urlsplit(self.path).path
        if not path.startswith(BASE):
            self.send_error(404)
            return None
        return super().send_head()

    def translate_path(self, path):
        return super().translate_path(path[len(BASE) - 1:])

    def list_directory(self, path):
        self.send_error(404)
        return None


def server(directory, port):
    if not (directory / "index.html").is_file():
        raise SystemExit("Construye primero el sitio con python -m radar.site")
    return ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(directory)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8919)
    with server(ROOT / "dist", parser.parse_args().port) as httpd:
        print(f"http://127.0.0.1:{httpd.server_port}{BASE}", flush=True)
        httpd.serve_forever()
