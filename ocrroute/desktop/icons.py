# coding=utf-8
"""
Vector icons for the desktop app, rendered from inline SVG paths with QtSvg so they stay crisp at any DPI and
recolour with the theme (no binary assets in the repository).
"""
from __future__ import absolute_import, division, print_function

from PyQt5.QtCore import QByteArray, QRectF, Qt
from PyQt5.QtGui import QIcon, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer

_PATHS = {
    'dashboard': 'M3 12h7V3H3zM14 21h7v-9h-7zM14 3v6h7V3zM3 21h7v-6H3z',
    'scan': 'M4 7V5a1 1 0 0 1 1-1h2M17 4h2a1 1 0 0 1 1 1v2M20 17v2a1 1 0 0 1-1 1h-2M7 20H5a1 1 0 0 1-1-1v-2M7 12h10M7 9h6M7 15h8',
    'batch': 'M4 7h16v13H4zM8 7V4h8v3M9 12h6',
    'engines': 'M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1M12 8a4 4 0 1 0 0 8 4 4 0 1 0 0-8',
    'providers': 'M12 3l8 4v10l-8 4-8-4V7zM12 12l8-4M12 12v9M12 12L4 8',
    'routes': 'M5 4a2 2 0 1 0 0 4 2 2 0 1 0 0-4M19 16a2 2 0 1 0 0 4 2 2 0 1 0 0-4M7 6h6a4 4 0 0 1 4 4a4 4 0 0 1-4 4H9a3 3 0 0 0-3 3',
    'history': 'M4 6h16M4 12h16M4 18h10',
    'tools': 'M14.7 6.3a4 4 0 0 0 5 5L13 18a2.5 2.5 0 0 1-3.5-3.5l6.7-6.7ZM4 20l3-3',
    'settings': 'M12 9a3 3 0 1 0 0 6 3 3 0 1 0 0-6M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1',
    'doctor': 'M12 21s-7-4.5-7-11a7 7 0 0 1 14 0c0 6.5-7 11-7 11zM9 10h2l1-2 1 4 1-2h1',
    'endpoints': 'M4 6h16v5H4zM4 13h16v5H4zM7 8.5h.01M7 15.5h.01',
    'sun': 'M12 8a4 4 0 1 0 0 8 4 4 0 1 0 0-8M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
    'moon': 'M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z',
    'play': 'M7 4l12 8-12 8z',
    'folder': 'M3 6h6l2 2h10v11H3z',
    'clipboard': 'M9 3h6v3H9zM7 5H5v16h14V5h-2',
    'capture': 'M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M12 9a3 3 0 1 0 0 6 3 3 0 1 0 0-6',
    'refresh': 'M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5',
    'ok': 'M4 12l5 5L20 7',
    'warn': 'M12 3l10 18H2zM12 10v5M12 18v.5',
}

_PAGE_ICONS = {'Dashboard': 'dashboard', 'Endpoints': 'endpoints', 'Scan': 'scan', 'Batch': 'batch', 'Engines': 'engines',
               'Providers & credentials': 'providers', 'Routes': 'routes', 'History': 'history', 'Tools': 'tools',
               'Settings': 'settings', 'Doctor': 'doctor'}
_cache = {}


def svg(name, color='#1B2230', strokeWidth=1.7):
    """
    :param name: str  key of _PATHS
    :param color: str  CSS colour
    :param strokeWidth: float
    :return: bytes  a complete SVG document
    """
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{}" stroke-width="{}" '
            'stroke-linecap="round" stroke-linejoin="round"><path d="{}"/></svg>').format(color, strokeWidth, _PATHS[name]).encode('utf-8')


def icon(name, color='#1B2230', size=20):
    """
    :param name: str
    :param color: str
    :param size: int  logical pixels
    :return: QIcon
    """
    key = (name, color, size)
    if key in _cache:
        return _cache[key]
    renderer = QSvgRenderer(QByteArray(svg(name, color)))
    result = QIcon()
    for scale in (1, 2):
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p, QRectF(0, 0, size * scale, size * scale))
        p.end()
        pm.setDevicePixelRatio(scale)
        result.addPixmap(pm)
    _cache[key] = result
    return result


def pageIcon(title, color='#1B2230', size=20):
    """
    :param title: str  Page.title
    :return: QIcon
    """
    return icon(_PAGE_ICONS.get(title, 'dashboard'), color, size)


__all__ = ['icon', 'pageIcon', 'svg']
