"""Tag wrangling: profiles, search, cache, canonical resolution, and rules."""

from ao3kit.tags.cache import default_tag_cache_path, DEFAULT_TAG_CACHE_TTL_DAYS
from ao3kit.tags.suggest import suggest_tag_names
from ao3kit.tags.metadata import (
    TagCache,
    TagProfile,
    TagRef,
    TagResolver,
    TagSearchCriteria,
    build_tag_search_url,
    fetch_tag_profile,
    parse_tag_page,
    parse_tag_search_page,
    parse_tag_search_url,
    parse_tag_set_page,
    parse_tag_sets_search_page,
    tag_url,
)
from ao3kit.tags.mappings import TagMapping, load_mappings, merge_mapping_rules
from ao3kit.tags.rules import (
    TagRule,
    TagRulesConfig,
    TagRulesEngine,
    load_tag_rules,
)

__all__ = [
    "default_tag_cache_path",
    "DEFAULT_TAG_CACHE_TTL_DAYS",
    "TagCache",
    "TagProfile",
    "TagRef",
    "TagResolver",
    "TagMapping",
    "TagRule",
    "TagRulesConfig",
    "TagRulesEngine",
    "TagSearchCriteria",
    "build_tag_search_url",
    "fetch_tag_profile",
    "load_mappings",
    "load_tag_rules",
    "merge_mapping_rules",
    "parse_tag_page",
    "parse_tag_search_page",
    "parse_tag_search_url",
    "parse_tag_set_page",
    "parse_tag_sets_search_page",
    "tag_url",
    "suggest_tag_names",
]
