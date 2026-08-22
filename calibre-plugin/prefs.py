# -*- coding: utf-8 -*-

from calibre.utils.config import JSONConfig

PREFS_NAMESPACE = 'plugins/ao3_scraper'

prefs = JSONConfig(PREFS_NAMESPACE)
prefs.defaults = {
    'setup_complete': False,
    'last_jsonl_path': '',
    'last_scrape_url': '',
    'last_tag_id': '',
    'last_query': '',
    'last_max_results': '25',
    'download_epubs': True,
    'simplify_tags': False,
    'update_existing': True,
    'import_full_series': False,
    'ao3kit_project': '',
    'ao3kit_python': '',
    'ao3_username': '',
    'ao3_password': '',
}


def plugin_runtime_settings() -> dict:
    """Login and import defaults for ao3kit subprocesses."""
    return {
        'ao3_username': prefs.get('ao3_username') or '',
        'ao3_password': prefs.get('ao3_password') or '',
        'include_series': bool(prefs.get('import_full_series', False)),
    }
