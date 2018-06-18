import asyncio
import ipaddress
import json
import logging

from aiohttp import web, ClientSession, ClientConnectionError

__all__ = ['HTTPStation', 'HTTPServer']

class HTTPClient:
    def __init__(self):
        self._url = None

    @property
    def url(self):
        return self._url
    @url.setter
    def url(self, url):
        self._url = url

    async def post(self, absolute_url, json_str_payload):
        async with ClientSession() as session:
            url = self._url + absolute_url
            payload = json.dumps(json_str_payload)
            headers = {'Content-Type': 'application/json'}
            async with session.post(url, data=payload, headers=headers) as resp:
                return resp

class HTTPStation:
    def __init__(self, station):
        self._station = station
        self._client = HTTPClient()
        self._port = None
        self._server_host = None
        self._server_port = None
        self._registered = False
        self._reg_future = None
        self._notify_reg_succeeded = None
        self._notify_reg_failed = None

    @property
    def port(self):
        return self._port
    @port.setter
    def port(self, port):
        # Only settable once
        if self._port is not None:
            raise RuntimeError('Attempted to change HTTP port')
        self._port = port

    @property
    def server_host(self):
        return self._server_host
    @server_host.setter
    def server_host(self, host):
        self._server_host = host
        self._update_server_url()

    @property
    def server_port(self):
        return self._server_port
    @server_port.setter
    def server_port(self, port):
        self._server_port = port
        self._update_server_url()

    @property
    def registered(self):
        return self._registered

    @property
    def notify_reg_succeeded(self):
        return self._notify_reg_succeeded
    @notify_reg_succeeded.setter
    def notify_reg_succeeded(self, notify):
        self._notify_reg_succeeded = notify

    @property
    def notify_reg_failed(self):
        return self._notify_reg_failed
    @notify_reg_failed.setter
    def notify_reg_failed(self, notify):
        self._notify_reg_failed = notify

    def register(self, attempts=10):
        '''Attempt to register with the given number of attempts.'''
        if self._reg_future is not None:
            return
        self._reg_future = asyncio.ensure_future(self._register(attempts))
        self._reg_future.add_done_callback(self._register_notify)

    async def _register(self, attempts):
        if self._client.url is None:
            raise RuntimeError('Server URL not set')

        logging.debug('Registering with %s:%d', self.server_host, self.server_port)
        registration_data = {'http_port': self.port}
        att = 0
        while att < attempts:
            try:
                resp = await self._client.post('/registration', registration_data)
                if resp.status < 300:
                    self._registered = True
                    logging.info('Registration successful')
                else:
                    self._registered = False
                    logging.warning('Registration rejected with status %d', resp.status)
                return
            except ClientConnectionError:
                pass
            except asyncio.CancelledError:
                return
            await asyncio.sleep(1)
            att += 1

        self._registered = False
        logging.warning('Registration unsuccessful after %d attempts', attempts)

    def _register_notify(self, future):
        self._reg_future = None
        if future.cancelled():
            return
        if future.exception():
            raise future.exception()
        if self._registered:
            if self._notify_reg_succeeded:
                self._notify_reg_succeeded()
        else:
            if self._notify_reg_failed:
                self._notify_reg_failed()

    def _update_server_url(self):
        if self._server_host and self._server_port:
            self._client.url = 'http://{self.server_host}:{self.server_port:d}'.format(self=self)
        else:
            self._client.url = None

class HTTPServer:
    _ROUTES_GET  = ({'url':     '/ping',
                     'handler': 'handle_ping'},)
    _ROUTES_POST = ({'url':     '/stream-data',
                     'handler': 'handle_stream_data'},
                    {'url':     '/volume',
                     'handler': 'handle_volume'})

    def __init__(self, station):
        self._station = station
        self._web_app = web.Application()
        self._web_server = None
        self._get_handler = HTTPServerGETHandler(self._station)
        self._post_handler = HTTPServerPOSTHandler(self._station)
        self._setup_routes()

    @property
    def get_handler(self):
        return self._get_handler
    @property
    def post_handler(self):
        return self._post_handler

    def start(self, host, port):
        if host:
            # Raise if the host is not an IP address
            ipaddress.ip_address(host)
        else:
            # Default to all interfaces if listening host is not given
            host = '0.0.0.0'

        loop = asyncio.get_event_loop()
        handler = self._web_app.make_handler(access_log=None)
        self._web_server = loop.create_server(handler, host, port)
        # Wait until the HTTP server starts
        loop.run_until_complete(self._web_server)
        logging.info('HTTP server running on %s:%d', host, port)

    def stop(self):
        if self._web_server is not None:
            self._web_server.close()
            self._web_server = None
            logging.info('HTTP server stopped')

    def _setup_routes(self):
        router = self._web_app.router
        # Install GET routes
        for route in self._ROUTES_GET:
            router.add_get(route['url'],
                           getattr(self._get_handler, route['handler']))
        # Install POST routes
        for route in self._ROUTES_POST:
            router.add_post(route['url'],
                            getattr(self._post_handler, route['handler']))

class HTTPServerGETHandler:
    def __init__(self, station):
        self._station = station
        self._ping_notify = None

    @property
    def ping_notify(self):
        return self._ping_notify
    @ping_notify.setter
    def ping_notify(self, notify):
        self._ping_notify = notify

    async def handle_ping(self, _):
        '''Ping the station.'''
        if self._ping_notify:
            self._ping_notify()
        return web.HTTPNoContent()

class HTTPServerPOSTHandler:
    def __init__(self, station):
        self._station = station

    async def handle_stream_data(self, req):
        if req.has_body and req.content_type == 'application/json':
            data = await req.json()
            if isinstance(data, dict):
                self._station.gst_process.update_stream(data)
                return web.HTTPNoContent()
            else:
                return web.HTTPBadRequest(text='Invalid stream data, array expected')
        else:
            return web.HTTPBadRequest(text='JSON location data required')

    async def handle_volume(self, req):
        if req.has_body and req.content_type == 'application/json':
            try:
                data = await req.json()
                self._station.gst_process.update_volume(int(data['value']))
                return web.HTTPNoContent()
            except (KeyError, ValueError):
                return web.HTTPBadRequest(text='Invalid volume value')
        else:
            return web.HTTPBadRequest(text='JSON location data required')
