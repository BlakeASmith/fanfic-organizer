# -*- coding: utf-8 -*-

from calibre.utils.config import JSONConfig

PREFS_NAMESPACE = 'plugins/ao3_scraper'

prefs = JSONConfig(PREFS_NAMESPACE)
prefs.defaults = {
    'setup_complete': False,
    'last_jsonl_path': '',
}
