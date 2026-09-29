# coding=utf-8
"""Embedded server: runs uvicorn in a QThread on a free local port and mints an admin key for this session."""
from __future__ import absolute_import, division, print_function

import socket
import time

from ManyQt.QtCore import QThread, Signal


def freePort():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return int(s.getsockname()[1])


class EmbeddedServer(QThread):
    ready = Signal(str, str)  # base_url, api_key
    failed = Signal(str)

    def __init__(self, port=None):
        super(EmbeddedServer, self).__init__()
        self.__m_port = port or freePort()
        self.__m_server = None
        self.__m_stopping = False

    def run(self):
        try:
            import os

            import uvicorn

            os.environ['OCRROUTE_PORT'] = str(self.__m_port)
            from ocrroute.config import resetSettings

            resetSettings()
            from ocrroute.api.app import createApp
            from ocrroute.runtime.context import buildContext

            buildContext()
            api_key = self._mintKey()
            app = createApp(include_panel=True)
            config = uvicorn.Config(app, host='127.0.0.1', port=self.__m_port, log_level='warning', lifespan='on')
            self.__m_server = uvicorn.Server(config)
            import threading

            def _wait():
                import requests

                for _ in range(200):
                    try:
                        if requests.get('http://127.0.0.1:{}/v1/health'.format(self.__m_port), timeout=0.5).ok:
                            self.ready.emit('http://127.0.0.1:{}'.format(self.__m_port), api_key)
                            return
                    except Exception:  # noqa: BLE001
                        time.sleep(0.1)
                self.failed.emit('embedded server did not become ready')

            from ocrroute.runtime import lifecycle

            while True:
                lifecycle.register(self.__m_server, 'embedded')
                threading.Thread(target=_wait, daemon=True).start()
                self.__m_server.run()
                if self.__m_stopping or not lifecycle.restartRequested():
                    break
                lifecycle.reset()  # Restart from the UI: rebuild context and app on the same port
                from ocrroute.runtime.context import resetContext

                resetContext()
                resetSettings()
                buildContext()
                api_key = self._mintKey()
                self.__m_server = uvicorn.Server(uvicorn.Config(createApp(include_panel=True), host='127.0.0.1',
                                                                port=self.__m_port, log_level='warning', lifespan='on'))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))

    @staticmethod
    def _mintKey():
        from ocrroute.crypto import hashApiKey, newApiKey
        from ocrroute.db.models import ApiKey
        from ocrroute.db.session import sessionScope

        raw = newApiKey()
        with sessionScope() as s:
            s.add(
                ApiKey(name='desktop-embedded-session', key_hash=hashApiKey(raw), key_prefix=raw[:10], scopes=['admin'])
            )
        return raw

    def stop(self):
        self.__m_stopping = True
        if self.__m_server is not None:
            self.__m_server.should_exit = True
        self.wait(5000)

    def getPort(self):
        """
        :return: any
        """
        return self.__m_port

    def setPort(self, port):
        """
        :param port: any
        """
        self.__m_port = port

    port = property(fget=getPort, fset=setPort)
