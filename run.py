"""Start DataMedic:  python run.py  [--host 127.0.0.1] [--port 5000] [--open]"""
import argparse
import threading
import webbrowser

from waitress import serve

from datamedic import config, create_app


def main():
    ap = argparse.ArgumentParser(description="DataMedic server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--open", action="store_true", help="open the app in your browser")
    args = ap.parse_args()
    app = create_app(start_cleanup=True)
    url = f"http://{args.host}:{args.port}"
    print(f"DataMedic running at {url}  (max upload {config.MAX_FILE_BYTES:,} bytes = 500 MB)")
    print("Press Ctrl+C to stop.")
    if args.open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    serve(app, host=args.host, port=args.port, threads=8,
          max_request_body_size=config.MAX_CONTENT_LENGTH,
          channel_timeout=600, cleanup_interval=30)


if __name__ == "__main__":
    main()
