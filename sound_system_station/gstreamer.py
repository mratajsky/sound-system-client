import collections
import logging
import multiprocessing
import queue

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstNet', '1.0')
from gi.repository import GLib, Gst, GstNet

Gst.init(None)

Message = collections.namedtuple('Message', ['t', 'args'])
Message.__new__.__defaults__ = ([],)

class GStreamerProcess:
    # Message types
    MSG_T_STOP_LOOP = 0             # Stop the main loop
    MSG_T_STOP_STREAM = 1           # Stop the stream
    MSG_T_UPDATE_STREAM_DATA = 2    # Start or rebuild the stream
    MSG_T_UPDATE_VOLUME = 3         # Update volume of a running stream

    def __init__(self):
        self._queue = multiprocessing.Queue()
        self._process = multiprocessing.Process(target=self._entry)
        self._loop = None
        self._gstreamer = None

    def start(self):
        logging.debug('Starting gstreamer process')
        self._process.start()

    def stop(self):
        message = Message(t=self.MSG_T_STOP_LOOP)
        self._queue.put(message)
        self._process.join()

    def stop_stream(self):
        message = Message(t=self.MSG_T_STOP_STREAM)
        self._queue.put(message)

    def update_stream(self, data):
        message = Message(t=self.MSG_T_UPDATE_STREAM_DATA, args=data)
        self._queue.put(message)

    def update_volume(self, data):
        message = Message(t=self.MSG_T_UPDATE_VOLUME, args=data)
        self._queue.put(message)

    def _ipc_watch(self):
        try:
            item = self._queue.get_nowait()
        except queue.Empty:
            return True
        if item.t == GStreamerProcess.MSG_T_STOP_LOOP:
            self._gstreamer.stop()
            self._loop.quit()
        elif item.t == GStreamerProcess.MSG_T_STOP_STREAM:
            self._gstreamer.stop()
        elif item.t == GStreamerProcess.MSG_T_UPDATE_STREAM_DATA:
            self._gstreamer.stream_data = item.args
            if not self._gstreamer.running:
                # Changing stream data restarts the pipeline if it's
                # already running, but doesn't start it initially
                self._gstreamer.start()
        elif item.t == GStreamerProcess.MSG_T_UPDATE_VOLUME:
            self._gstreamer.volume = item.args
        # Return True so that this function is run again
        return True

    def _entry(self):
        # Create a GLib main loop to be used by the gstreamer
        self._loop = GLib.MainLoop()
        self._gstreamer = GStreamer()
        # Check the IPC pipe every second
        GLib.timeout_add_seconds(1, self._ipc_watch)
        try:
            self._loop.run()
        except (KeyboardInterrupt, SystemExit):
            self._loop.quit()

class GStreamer:
    def __init__(self):
        self._pipeline = None
        self._stream_data = None
        self._volume = None

    def start(self):
        if not self.running:
            if not self._build_stream():
                return False
            logging.debug('Starting stream')
            self._pipeline.set_state(Gst.State.PLAYING)
        return True

    def stop(self):
        if self.running:
            logging.debug('Stopping stream')
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
            self._volume = None

    @property
    def running(self):
        return self._pipeline and self._pipeline.target_state == Gst.State.PLAYING

    @property
    def stream_data(self):
        return self._stream_data
    @stream_data.setter
    def stream_data(self, data):
        if 'pipeline' not in data:
            logging.error('Invalid stream data')
            return
        if self._stream_data == data:
            return
        self._stream_data = data
        if self.running:
            logging.debug('Pipeline of running stream has changed')
            self.stop()
            self.start()

    # We use 0-100 volume range
    @property
    def volume(self):
        if self._volume is not None:
            return self._volume.get_property('volume') * 100
    @volume.setter
    def volume(self, value):
        if self._volume is not None:
            value = max(0, min(100, value))
            logging.info('Changing stream volume to %d', value)
            self._volume.set_property('volume', value / 100.0)
        else:
            logging.warning('Not changing stream volume: no volume element in the pipeline')

    def _build_stream(self):
        logging.debug('Building stream pipeline: %s', self._stream_data['pipeline'])
        try:
            self._pipeline = Gst.parse_launch(self._stream_data['pipeline'])
        except GLib.Error as e:
            logging.error('New pipeline failed to be built: %s', e.message)
            return False

        # Setup the pipeline clock if given by the server
        clock = self._stream_data.get('clock')
        if isinstance(clock, dict):
            host = self._stream_data['clock'].get('host', None)
            port = self._stream_data['clock'].get('port', None)
            if port is None:
                port = 123
            if host is None:
                logging.warning('Invalid clock description in stream data')
            else:
                logging.debug('Pipeline NTP server: %s:%d', host, port)
                clock = GstNet.NtpClock.new('mrs-cup-cake-clock', host, int(port), 0)
                self._pipeline.use_clock(clock)

        # Find the volume element in the pipeline
        volume_name = self._stream_data.get('volume_name')
        if volume_name:
            self._volume = self._pipeline.get_by_name(volume_name)
            if self._volume is not None:
                logging.debug('Pipeline volume element: %s', volume_name)
            else:
                logging.warning('Invalid pipeline volume element name: %s', volume_name)
        if self._volume is None:
            logging.warning('No volume element in the pipeline')

        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message::error', self._on_bus_error)
        bus.connect('message::state-changed', self._on_bus_state_changed)
        return True

    def _on_bus_error(self, _, message):
        err, _ = message.parse_error()
        logging.error('Stream error: %s', err)
        self.stop()

    def _on_bus_state_changed(self, _, message):
        # Only care about state changes of the sink
        if message.src != self._pipeline:
            return
        prev, new, _ = message.parse_state_changed()
        logging.debug('Pipeline state changed: %s -> %s',
                      Gst.Element.state_get_name(prev),
                      Gst.Element.state_get_name(new))
