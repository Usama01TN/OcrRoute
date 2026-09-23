# coding=utf-8
"""QGraphicsView with zoom/pan, word/line overlay boxes, hover tooltips and a crop (region) tool."""
from __future__ import absolute_import, division, print_function

from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QGraphicsRectItem, QGraphicsScene, QGraphicsView

ACCENT = QColor(245, 180, 0)
INK = QColor(27, 34, 48)


class _WordItem(QGraphicsRectItem):
    def __init__(self, rect, word, index):
        super(_WordItem, self).__init__(rect)
        self.word, self.index = word, index
        self.setPen(QPen(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 230), 0))
        self.setBrush(QBrush(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 70)))
        tip = word.get('WordText', '')
        if 'Confidence' in word:
            tip += '  ({})'.format(word['Confidence'])
        self.setToolTip(tip)
        self.setAcceptHoverEvents(True)

    def hoverEnterEvent(self, event):
        self.setBrush(QBrush(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 140)))
        super(_WordItem, self).hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setBrush(QBrush(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 70)))
        super(_WordItem, self).hoverLeaveEvent(event)


class ImageViewer(QGraphicsView):
    wordClicked = pyqtSignal(int)  # line index
    regionSelected = pyqtSignal(int, int, int, int)

    def __init__(self, parent=None):
        super(ImageViewer, self).__init__(parent)
        self.__m_scene = QGraphicsScene(self)
        self.setScene(self.__m_scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.__m_pix = None
        self.__m_boxes = []
        self.__m_showBoxes = True
        self.__m_cropMode = False
        self.__m_rubber = None
        self.__m_origin = QPointF()

    # ------------------------------------------------------------------ image
    def setImageBytes(self, data):
        img = QImage.fromData(data)
        self.__m_scene.clear()
        self.__m_boxes = []
        self.__m_pix = self.__m_scene.addPixmap(QPixmap.fromImage(img))
        self.__m_scene.setSceneRect(QRectF(self.__m_pix.pixmap().rect()))
        self.fit()

    def clear(self):
        self.__m_scene.clear()
        self.__m_pix = None
        self.__m_boxes = []

    def fit(self):
        if self.__m_pix is not None:
            self.fitInView(self.__m_pix, Qt.KeepAspectRatio)

    def zoom(self, factor):
        self.scale(factor, factor)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.zoom(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
        else:
            super(ImageViewer, self).wheelEvent(event)

    # ------------------------------------------------------------------ overlay
    def setResult(self, result, page=1):
        for b in self.__m_boxes:
            self.__m_scene.removeItem(b)
        self.__m_boxes = []
        if not result or self.__m_pix is None:
            return
        for li, line in enumerate(result.get('TextOverlay', {}).get('Lines', [])):
            if int(line.get('Page', 1)) != page:
                continue
            words = line.get('Words', [])
            if words:
                x1 = min(w['Left'] for w in words)
                y1 = min(w['Top'] for w in words)
                x2 = max(w['Left'] + w['Width'] for w in words)
                y2 = max(w['Top'] + w['Height'] for w in words)
                lb = QGraphicsRectItem(QRectF(x1 - 2, y1 - 2, x2 - x1 + 4, y2 - y1 + 4))
                lb.setPen(QPen(QColor(INK.red(), INK.green(), INK.blue(), 150), 0))
                self.__m_scene.addItem(lb)
                self.__m_boxes.append(lb)
            for w in words:
                item = _WordItem(QRectF(w['Left'], w['Top'], w['Width'], w['Height']), w, li)
                self.__m_scene.addItem(item)
                self.__m_boxes.append(item)
        self.setBoxesVisible(self.__m_showBoxes)

    def setBoxesVisible(self, visible):
        self.__m_showBoxes = visible
        for b in self.__m_boxes:
            b.setVisible(visible)

    # ------------------------------------------------------------------ crop
    def setCropMode(self, on):
        self.__m_cropMode = on
        self.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.ScrollHandDrag)
        self.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)

    def beginCrop(self, scenePoint):
        """
        :param scenePoint: QPointF - rubber-band origin in scene coordinates
        """
        self.__m_origin = scenePoint
        self.__m_rubber = QGraphicsRectItem(QRectF(self.__m_origin, self.__m_origin))
        self.__m_rubber.setPen(QPen(ACCENT, 0, Qt.DashLine))
        self.__m_scene.addItem(self.__m_rubber)

    def updateCrop(self, scenePoint):
        """
        :param scenePoint: QPointF
        """
        if self.__m_rubber is not None:
            self.__m_rubber.setRect(QRectF(self.__m_origin, scenePoint).normalized())

    def endCrop(self):
        """
        Finish the rubber band; emits ``regionSelected`` when the rectangle is large enough.

        :return: bool - True when a region was emitted
        """
        if self.__m_rubber is None:
            return False
        r = self.__m_rubber.rect().intersected(self.__m_scene.sceneRect())
        self.__m_scene.removeItem(self.__m_rubber)
        self.__m_rubber = None
        emitted = False
        if r.width() > 4 and r.height() > 4:
            self.regionSelected.emit(int(r.x()), int(r.y()), int(r.width()), int(r.height()))
            emitted = True
        self.setCropMode(False)
        return emitted

    def getBoxes(self):
        """
        :return: list[QGraphicsRectItem] - overlay items currently drawn
        """
        return list(self.__m_boxes)

    boxes = property(fget=getBoxes)

    def mousePressEvent(self, event):
        if self.__m_cropMode and event.button() == Qt.LeftButton and self.__m_pix is not None:
            self.beginCrop(self.mapToScene(event.pos()))
            return
        item = self.itemAt(event.pos())
        if isinstance(item, _WordItem):
            self.wordClicked.emit(item.index)
        super(ImageViewer, self).mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.__m_cropMode and self.__m_rubber is not None:
            self.updateCrop(self.mapToScene(event.pos()))
            return
        super(ImageViewer, self).mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.__m_cropMode and self.__m_rubber is not None:
            self.endCrop()
            return
        super(ImageViewer, self).mouseReleaseEvent(event)
