"""FTP server the camera uploads to (Sony A7 IV: Network > FTP Transfer)."""
import logging
import threading
from pathlib import Path

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import ThreadedFTPServer

from . import config, ingest

log = logging.getLogger("ftp")


class CameraHandler(FTPHandler):
    def on_file_received(self, file):
        try:
            ingest.file_received(Path(file))
        except Exception:
            log.exception("could not ingest %s", file)

    def on_incomplete_file_received(self, file):
        log.warning("incomplete upload removed: %s", file)
        Path(file).unlink(missing_ok=True)


def start():
    authorizer = DummyAuthorizer()
    authorizer.add_user(config.FTP_USER, config.FTP_PASSWORD, str(config.INCOMING), perm="elradfmw")
    CameraHandler.authorizer = authorizer
    lo, hi = (int(p) for p in config.FTP_PASSIVE_PORTS.split("-"))
    CameraHandler.passive_ports = range(lo, hi + 1)
    if config.FTP_PUBLIC_HOST:
        CameraHandler.masquerade_address = config.FTP_PUBLIC_HOST
    server = ThreadedFTPServer(("0.0.0.0", config.FTP_PORT), CameraHandler)
    threading.Thread(target=server.serve_forever, daemon=True, name="ftp").start()
    log.info("FTP listening on %s", config.FTP_PORT)
