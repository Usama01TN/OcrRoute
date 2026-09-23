# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QVBoxLayout, QWidget

from ocrroute.desktop.pages.base import Page, fmtMs


class Kpi(QFrame):
    def __init__(self, label):
        super(Kpi, self).__init__()
        self.setObjectName('card')
        lay = QVBoxLayout(self)
        self.value = QLabel('-')
        self.value.setObjectName('kpiValue')
        self.label = QLabel(label)
        self.label.setObjectName('muted')
        lay.addWidget(self.value)
        lay.addWidget(self.label)


class BarChart(QWidget):
    """Tiny QPainter bar chart (no QtCharts dependency)."""

    def __init__(self):
        super(BarChart, self).__init__()
        self.series = []  # day, ok, cached, failed
        self.setMinimumHeight(160)

    def setData(self, daily):
        self.series = [(d['day'][5:], d.get('succeeded', 0), d.get('cached', 0), d.get('failed', 0)) for d in daily]
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(217, 221, 227), 1))
        p.drawLine(30, h - 22, w - 8, h - 22)
        if not self.series:
            p.setPen(QColor(107, 116, 132))
            p.drawText(self.rect(), Qt.AlignCenter, 'No runs in this window')
            return
        top = max(1, max(a + b + c for _, a, b, c in self.series))
        n = len(self.series)
        bw = max(6, (w - 40) / n * 0.6)
        for i, (day, ok, cached, failed) in enumerate(self.series):
            x = 34 + i * (w - 40) / n
            y = h - 22
            for val, col in ((ok, QColor(27, 34, 48)), (cached, QColor(245, 180, 0)), (failed, QColor(200, 64, 42))):
                bh = (h - 40) * val / top
                p.fillRect(int(x), int(y - bh), int(bw), int(bh), col)
                y -= bh
            p.setPen(QColor(107, 116, 132))
            p.drawText(int(x - 6), h - 6, day)


class DashboardPage(Page):
    title = 'Dashboard'
    subtitle = 'Runs, latency, spend and provider health at a glance.'

    def __init__(self, state):
        super(DashboardPage, self).__init__(state)
        self.button('Refresh', self.refresh)
        grid = QGridLayout()
        self.k_runs, self.k_lat, self.k_pages, self.k_cost = (
            Kpi('runs · 24h'),
            Kpi('p50 / p95'),
            Kpi('pages · cache hit rate'),
            Kpi('estimated spend · month'),
        )
        for i, k in enumerate((self.k_runs, self.k_lat, self.k_pages, self.k_cost)):
            grid.addWidget(k, 0, i)
        self.root.addLayout(grid)
        row = QHBoxLayout()
        chart_card = QFrame()
        chart_card.setObjectName('card')
        cl = QVBoxLayout(chart_card)
        cl.addWidget(QLabel('Runs by day'))
        self.chart = BarChart()
        cl.addWidget(self.chart)
        row.addWidget(chart_card, 2)
        health_card = QFrame()
        health_card.setObjectName('card')
        hl = QVBoxLayout(health_card)
        hl.addWidget(QLabel('Provider health'))
        self.health = QLabel('-')
        self.health.setWordWrap(True)
        hl.addWidget(self.health)
        hl.addStretch(1)
        row.addWidget(health_card, 1)
        self.root.addLayout(row)
        feed_card = QFrame()
        feed_card.setObjectName('card')
        fl = QVBoxLayout(feed_card)
        fl.addWidget(QLabel('Recent runs'))
        self.feed = QListWidget()
        fl.addWidget(self.feed)
        self.root.addWidget(feed_card, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(15000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def refresh(self):
        self.call(lambda c: (c.stats(24), c.runs(limit=25), c.providers()), self._show)

    def _show(self, data):
        stats, runs, providers = data
        s = stats['summary']
        self.k_runs.value.setText(
            "{}  <span style='font-size:12px;color:#6B7484'>{}% ok</span>".format(s['runs'], s['success_rate'])
        )
        self.k_lat.value.setText('{} / {} ms'.format(s['p50_ms'], s['p95_ms']))
        self.k_pages.value.setText(
            "{}  <span style='font-size:12px;color:#6B7484'>{}%</span>".format(s['pages'], s['cache_hit_rate'])
        )
        self.k_cost.value.setText(
            "${:.4f}  <span style='font-size:12px;color:#6B7484'>${:.3f}</span>".format(
                s['cost_cents'] / 100, s['month_cost_cents'] / 100
            )
        )
        self.chart.setData(stats['daily'])
        if providers:
            self.health.setText(
                '   '.join(
                    '{} {} ({})'.format('●' if p['health'] == 'healthy' else '○', p['label'], p['health'])
                    for p in providers
                )
            )
        else:
            self.health.setText(self.tr('No providers configured - the built-in auto route uses local engines.'))
        self.feed.clear()
        for r in runs:
            self.feed.addItem(
                '{}  {:9}  {:14} {:16} {:>9}  {} chars'.format(
                    r['created_at'][11:19],
                    r['status'],
                    r['route'] or '-',
                    r['winning_engine'] or '-',
                    fmtMs(r['duration_ms']),
                    r['chars'],
                )
            )
