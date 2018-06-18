import asyncio

from zeroconf import ServiceBrowser, Zeroconf

class DiscoveryClientService:
    _SERVICE_TYPE = '_http._tcp.local.'
    _SERVICE_NAME = 'sound-system-server._http._tcp.local.'

    def __init__(self, loop, notify):
        self._zeroconf = Zeroconf()
        self._listener = self.ServiceListener(loop, notify)
        self._browser = None

    def start(self):
        self._browser = ServiceBrowser(self._zeroconf,
                                       self._SERVICE_TYPE,
                                       self._listener)

    def stop(self):
        if self._browser is not None:
            self._browser.cancel()
            self._browser = None

    class ServiceListener:
        def __init__(self, loop, notify):
            self._loop = loop
            self._notify = notify

        def add_service(self, zeroconf, t, name):
            if name == DiscoveryClientService._SERVICE_NAME :
                info = zeroconf.get_service_info(t, name)
                self._loop.call_soon_threadsafe(self._notify, name, info)

        def remove_service(self, zeroconf, t, name):
            pass
