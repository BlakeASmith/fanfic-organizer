# Fanfic Organizer: zip, Calibre install, GitHub release
# https://github.com/casey/just

set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

python := env_var_or_default("PYTHON", "python")

# List recipes
default:
    @just --list

# Build fanfic-organizer.zip (GitHub Release artifact)
build:
    {{ python }} makeplugin.py zip

# Install this checkout into Calibre (restart Calibre yourself)
load-dev:
    {{ python }} makeplugin.py install
    @echo "Restart Calibre to load the plugin."

# Next 0.x minor. `just release` / `just release patch` / `just release publish` / `just release patch publish`
release first="" second="":
    #!/usr/bin/env bash
    set -euo pipefail
    extra=()
    for arg in "{{ first }}" "{{ second }}"; do
      case "$arg" in
        "") ;;
        patch|--patch) extra+=(--patch) ;;
        publish|--publish) extra+=(--publish) ;;
        *) echo "usage: just release [patch] [publish]" >&2; exit 2 ;;
      esac
    done
    {{ python }} makeplugin.py release "${extra[@]}"
