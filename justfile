# Wranglekit: zip, Calibre install, GitHub release
# https://github.com/casey/just

set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

python := env_var_or_default("PYTHON", "python")

# List recipes
default:
    @just --list

# Build wranglekit.zip (GitHub Release artifact)
build:
    {{ python }} makeplugin.py zip

# Install this checkout into Calibre (restart Calibre yourself)
load-dev:
    {{ python }} makeplugin.py install
    @echo "Restart Calibre to load the plugin."

# Cut [Unreleased] into X.Y.Z. `just release 0.27.0` or `just release 0.27.0 publish`
release version publish="":
    #!/usr/bin/env bash
    set -euo pipefail
    extra=()
    case "{{ publish }}" in
      "") ;;
      publish|--publish) extra+=(--publish) ;;
      *) echo "usage: just release X.Y.Z [publish]" >&2; exit 2 ;;
    esac
    {{ python }} makeplugin.py release "{{ version }}" "${extra[@]}"
