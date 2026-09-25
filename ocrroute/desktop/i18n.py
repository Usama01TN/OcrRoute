# coding=utf-8
"""
Desktop translation: a QTranslator backed by the shared JSON catalogues in ``ocrroute/i18n``.

Every ``self.tr('…')`` call in the desktop code resolves through this translator, so the web panel and the
desktop app share one vocabulary and one set of catalogue files.
"""
from __future__ import absolute_import, division, print_function

from ManyQt.QtCore import QLocale, Qt, QTranslator

from ocrroute import i18n


class JsonTranslator(QTranslator):
    """
    JsonTranslator class.
    """

    def __init__(self, lang, parent=None):
        """
        :param lang: str  language code
        :param parent: QObject | None
        """
        super(JsonTranslator, self).__init__(parent)
        self.__m_lang = i18n.normalise(lang)
        self.__m_catalogue = i18n.catalogue(self.__m_lang)

    def getLang(self):
        """
        :return: str
        """
        return self.__m_lang

    def isEmpty(self):
        """
        :return: bool  Qt asks this to decide whether to install the translator
        """
        return False

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        """
        :param context: str  Qt context (class name) - ignored, catalogues are global
        :param sourceText: str
        :param disambiguation: str | None
        :param n: int
        :return: str  translation, or the source text when the catalogue has no entry
        """
        return self.__m_catalogue.get(sourceText) or sourceText

    lang = property(getLang)


def defaultLanguage(settings):
    """
    :param settings: QSettings
    :return: str  saved UI language, else the OS locale when we have a catalogue for it, else English
    """
    saved = settings.value('ui/language', '')
    if saved:
        return i18n.normalise(str(saved))
    return i18n.normalise(QLocale().name())


def install(app, lang):
    """
    Install (or replace) the translator on the application and set the layout direction.

    :param app: QApplication
    :param lang: str
    :return: JsonTranslator
    """
    old = getattr(app, '_ocrrouteTranslator', None)
    if old is not None:
        app.removeTranslator(old)
    translator = JsonTranslator(lang, app)
    if i18n.normalise(lang) != i18n.DEFAULT:  # English is the source language: nothing to install
        app.installTranslator(translator)
    app._ocrrouteTranslator = translator
    app.setLayoutDirection(Qt.RightToLeft if i18n.isRtl(lang) else Qt.LeftToRight)
    return translator


__all__ = ['JsonTranslator', 'defaultLanguage', 'install']
