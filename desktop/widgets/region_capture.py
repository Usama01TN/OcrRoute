# coding=utf-8
"""Frameless translucent full-screen rubber-band grabber: select a screen region, get PNG bytes back."""
from __future__ import absolute_import, division, print_function

from ManyQt.QtCore import QBuffer, QIODevice, QPoint, QRect, Qt, Signal
from ManyQt.QtGui import QColor, QGuiApplication, QPainter, QPen
from ManyQt.QtWidgets import QWidget


class RegionCapture(QWidget):
    captured = Signal(bytes)
    cancelled = Signal()

    def __init__(self):
        super(RegionCapture, self).__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        screen = QGuiApplication.primaryScreen()
        self.__m_shot = screen.grabWindow(0) if screen else None
        geo = screen.geometry() if screen else QRect(0, 0, 800, 600)
        self.setGeometry(geo)
        self.__m_start = QPoint()
        self.__m_end = QPoint()
        self.__m_dragging = False

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if self.__m_dragging:
            r = QRect(self.__m_start, self.__m_end).normalized()
            if self.__m_shot is not None:
                p.drawPixmap(
                    r,
                    self.__m_shot,
                    QRect(r.topLeft() * self.__m_shot.devicePixelRatio(), r.size() * self.__m_shot.devicePixelRatio()),
                )
            p.setPen(QPen(QColor(245, 180, 0), 2))
            p.drawRect(r)

    def mousePressEvent(self, e):
        self.__m_start = self.__m_end = e.pos()
        self.__m_dragging = True
        self.update()

    def mouseMoveEvent(self, e):
        self.__m_end = e.pos()
        self.update()

    def mouseReleaseEvent(self, e):
        r = QRect(self.__m_start, e.pos()).normalized()
        self.close()
        if self.__m_shot is None or r.width() < 4 or r.height() < 4:
            self.cancelled.emit()
            return
        dpr = self.__m_shot.devicePixelRatio()
        crop = self.__m_shot.copy(QRect(r.topLeft() * dpr, r.size() * dpr))
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        crop.save(buf, 'PNG')
        self.captured.emit(bytes(buf.data()))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
            self.cancelled.emit()
