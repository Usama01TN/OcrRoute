# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

from PyQt5.QtCore import QObject, QSettings, pyqtSignal

from ocrroute.desktop.client import OcrRouteClient


class AppState(QObject):
    """Connection + settings shared by all pages."""

    connected = pyqtSignal(str)  # base url
    disconnected = pyqtSignal(str)  # reason
    toast = pyqtSignal(str, bool)  # message, is_error
    themeChanged = pyqtSignal(str)  # light | dark | system
    languageChanged = pyqtSignal(str)  # language code

    def __init__(self):
        super(AppState, self).__init__()
        self.__m_settings = QSettings('OcrRoute', 'OcrRoute Desktop')
        self.__m_client = None
        self.__m_baseUrl = ''
        self.__m_embedded = True
        self.__m_engines = []
        self.__m_routes = []

    def connectTo(self, base_url, api_key, embedded=False):
        self.__m_client = OcrRouteClient(base_url, api_key)
        self.__m_baseUrl = base_url
        self.__m_embedded = embedded
        self.connected.emit(base_url)

    def apiKeyFor(self, url):
        try:
            import keyring  # type: ignore

            v = keyring.get_password('ocrroute-desktop', url)
            if v:
                return v
        except Exception:  # noqa: BLE001
            pass
        return str(self.__m_settings.value('servers/{}/api_key'.format(url), ''))

    def rememberServer(self, url, api_key):
        servers = set(self.savedServers())
        servers.add(url)
        self.__m_settings.setValue('servers/list', sorted(servers))
        try:
            import keyring  # type: ignore

            keyring.set_password('ocrroute-desktop', url, api_key)
            return
        except Exception:  # noqa: BLE001
            self.__m_settings.setValue('servers/{}/api_key'.format(url), api_key)

    def savedServers(self):
        v = self.__m_settings.value('servers/list', [])
        return list(v) if isinstance(v, (list, tuple)) else ([v] if v else [])

    def getSettings(self):
        """
        :return: any
        """
        return self.__m_settings

    def setSettings(self, settings):
        """
        :param settings: any
        """
        self.__m_settings = settings

    def getClient(self):
        """
        :return: any
        """
        return self.__m_client

    def setClient(self, client):
        """
        :param client: any
        """
        self.__m_client = client

    def getBaseUrl(self):
        """
        :return: any
        """
        return self.__m_baseUrl

    def setBaseUrl(self, baseUrl):
        """
        :param baseUrl: any
        """
        self.__m_baseUrl = baseUrl

    def getEmbedded(self):
        """
        :return: any
        """
        return self.__m_embedded

    def setEmbedded(self, embedded):
        """
        :param embedded: any
        """
        self.__m_embedded = embedded

    def getEngines(self):
        """
        :return: any
        """
        return self.__m_engines

    def setEngines(self, engines):
        """
        :param engines: any
        """
        self.__m_engines = engines

    def getRoutes(self):
        """
        :return: any
        """
        return self.__m_routes

    def setRoutes(self, routes):
        """
        :param routes: any
        """
        self.__m_routes = routes

    settings = property(fget=getSettings, fset=setSettings)
    client = property(fget=getClient, fset=setClient)
    base_url = property(fget=getBaseUrl, fset=setBaseUrl)
    embedded = property(fget=getEmbedded, fset=setEmbedded)
    engines = property(fget=getEngines, fset=setEngines)
    routes = property(fget=getRoutes, fset=setRoutes)
