# -*- coding: utf-8 -*-

from calibre.utils.config import JSONConfig

PREFS_NAMESPACE = 'plugins/fanfic-organizer'
LEGACY_PREFS_NAMESPACE = 'plugins/ao3_scraper'

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
    'generate_covers': True,
    'set_calibre_cover': True,
    'context_menu_placed': False,
}


def _prefs_still_default() -> bool:
    return all(prefs.get(key, default) == default for key, default in prefs.defaults.items())


def _migrate_legacy_prefs() -> None:
    if not _prefs_still_default():
        return
    try:
        legacy = JSONConfig(LEGACY_PREFS_NAMESPACE)
    except Exception:
        return
    copied = False
    for key, default in prefs.defaults.items():
        value = legacy.get(key, default)
        if value != default:
            prefs[key] = value
            copied = True
    if copied:
        try:
            prefs.commit()
        except Exception:
            pass


_migrate_legacy_prefs()


def plugin_runtime_settings() -> dict:
    """Login and import defaults for ao3kit subprocesses."""
    return {
        'ao3_username': prefs.get('ao3_username') or '',
        'ao3_password': prefs.get('ao3_password') or '',
        'include_series': bool(prefs.get('import_full_series', False)),
    }
