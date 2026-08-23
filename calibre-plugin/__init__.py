# -*- coding: utf-8 -*-

from calibre.customize import InterfaceActionBase

__version__ = (0, 26, 1)
class FanficOrganizerBase(InterfaceActionBase):
    name = 'Fanfic Organizer'
    description = (
        'Search AO3, download EPUBs, generate covers, import into a Calibre '
        'library, complete selected books (series, EPUBs, tags), import series, '
        'fill Series on existing books, run searches '
        'and other work as background jobs (attach logs, detach, stop), warm '
        'the tag cache in the background, graph tag relationships, set up '
        'collection and tag rules, recompute or edit collections for selected '
        'books, and purge rare tags from the Tags column. Uses Fandom, '
        'Relationships, Collections, Original Tags, word count, and Calibre\'s '
        'Series field.'
    )
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Emily'
    version = __version__
    minimum_calibre_version = (5, 0, 0)

    actual_plugin = 'calibre_plugins.fanfic_organizer.ao3_plugin:FanficOrganizerPlugin'

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.fanfic_organizer.config import ConfigWidget
        return ConfigWidget(self.actual_plugin_)

    def save_settings(self, config_widget):
        config_widget.save_settings()
        ac = self.actual_plugin_
        if ac is not None:
            ac.apply_settings()
