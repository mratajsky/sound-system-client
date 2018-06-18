import asyncio
import logging
import signal
import socket
import time
from contextlib import suppress

from .gstreamer import GStreamerProcess
from .http import HTTPServer, HTTPStation

class Station:
    def __init__(self, config):
        self._config = config
        self._gst_process = GStreamerProcess()
        self._http_server = HTTPServer(self)
        self._http_station = HTTPStation(self)
        self._ping_time = time.time()
        self._http_server.get_handler.ping_notify = self._ping_notify
        self._discovery = None
        self._running = False
        self._timeout = config.getfloat('station', 'timeout', fallback=5)

    def start(self):
        self._start_gst()
        self._start_http()
        self._running = True
        loop = asyncio.get_event_loop()
        for signame in ('SIGINT', 'SIGTERM'):
            loop.add_signal_handler(getattr(signal, signame), self.stop)
        try:
            loop.run_forever()
            pending = asyncio.Task.all_tasks()
            for task in pending:
                task.cancel()
                # Await task to execute its cancellation
                with suppress(asyncio.CancelledError):
                    loop.run_until_complete(task)
        finally:
            loop.close()

    def stop(self):
        self._gst_process.stop()
        self._http_server.stop()
        self._running = False
        asyncio.get_event_loop().stop()

    @property
    def config(self):
        return self._config
    @property
    def gst_process(self):
        return self._gst_process
    @property
    def http_server(self):
        return self._http_server
    @property
    def http_station(self):
        return self._http_station

    def _discover_notify(self, _, info):
        server_host = socket.inet_ntoa(info.address)
        logging.debug('Discovered server %s:%d', server_host, info.port)
        self._discovery.stop()
        self._http_station.server_host = server_host
        self._http_station.server_port = info.port
        self._http_station.register()

    def _discover_server(self):
        loop = asyncio.get_event_loop()
        from .discovery import DiscoveryClientService
        self._discovery = DiscoveryClientService(loop, self._discover_notify)
        self._discovery.start()

    def _start_gst(self):
        self._gst_process.start()

    def _start_http(self):
        host = self.config.get('http', 'host', fallback=None)
        port = self.config.getint('http', 'port')
        self._http_server.start(host, port)

        self._http_station.port = port
        self._http_station.notify_reg_succeeded = self._http_notify_reg_succeeded
        self._http_station.notify_reg_failed = self._http_notify_reg_failed
        server_host = self.config.get('station', 'serverhost', fallback=None)
        if not server_host:
            self._discover_server()
        else:
            self._http_station.server_host = server_host
            self._http_station.server_port = self.config.getint(
                'station',
                'serverport', fallback=8080)
            self._http_station.register()

    def _http_notify_reg_failed(self):
        # If registration failed and the server was discovered automatically,
        # retry the discovery first. If the server host is fixed, just retry
        # registration.
        if self._discovery:
            self._discover_server()
        else:
            self._http_station.register()

    def _http_notify_reg_succeeded(self):
        self._ping_time = time.time()
        if self._timeout > 0:
            asyncio.ensure_future(self._ping_watch())

    def _ping_notify(self):
        self._ping_time = time.time()

    async def _ping_watch(self):
        while self._running:
            diff = time.time() - self._ping_time
            if diff > self._timeout:
                logging.debug('Server timeout')
                self._gst_process.stop_stream()
                if self._discovery:
                    self._discover_server()
                else:
                    self._http_station.register()
                break
            await asyncio.sleep(1)
