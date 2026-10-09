"""External-system adapters. Each has a ``base.py`` interface plus ``mock`` and
``live`` implementations; ``build_*`` factories import the selected one lazily so
live-only dependencies are never imported in mock mode."""
