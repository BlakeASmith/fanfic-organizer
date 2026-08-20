# -*- coding: utf-8 -*-

from calibre.customize import InterfaceActionBase

__version__ = (0, 1, 0)


class AO3ScraperBase(InterfaceActionBase):
    name = 'AO3 Scraper'
    description = 'Import AO3 JSONL metadata into Calibre.'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Emily'
    version = __version__
    minimum_calibre_version = (5, 0, 0)

    actual_plugin = 'calibre_plugins.ao3_scraper.ao3_plugin:AO3ScraperPlugin'

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.ao3_scraper.config import ConfigWidget
        return ConfigWidget(self.actual_plugin_)

    def save_settings(self, config_widget):
        config_widget.save_settings()
        ac = self.actual_plugin_
        if ac is not None:
            ac.apply_settings()
