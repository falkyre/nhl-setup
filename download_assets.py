import urllib.request
import os

files = [
    ("https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js", "web/static/js/xterm.min.js"),
    ("https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css", "web/static/css/xterm.min.css"),
    ("https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/addon-fit.min.js", "web/static/js/xterm-addon-fit.min.js"),
    ("https://cdn.socket.io/4.7.2/socket.io.min.js", "web/static/js/socket.io.min.js")
]

for url, path in files:
    print(f"Downloading {url} to {path}...")
    try:
        urllib.request.urlretrieve(url, path)
        print("Success.")
    except Exception as e:
        print(f"Failed: {e}")
