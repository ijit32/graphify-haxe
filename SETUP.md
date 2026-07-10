# graphify-haxe — Setup

Instructions for users migrating from the official `graphifyy` (PyPI) to this
fork, or cloning fresh.

## Prerequisites

- Python 3.10+
- `uv` (recommended) or `pipx`
- An existing `graphifyy` install is fine — the steps below replace it.

## Install from source

```bash
# 1. Clone the fork
git clone https://github.com/ijit32/graphify-haxe.git
cd graphify-haxe

# 2. Replace the existing PyPI graphifyy with the local fork
#    --reinstall uninstalls the old package, -e creates an editable
#    (live-code) install tied to this checkout.
uv tool install --reinstall -e .

# 3. Verify
graphify --version
```

> If you use `pipx` instead of `uv`:
> ```bash
> pipx uninstall graphifyy
> pipx install -e .
> ```

## Register the skill with your AI assistants

```bash
# Claude Code
graphify claude install

# OpenCode
graphify opencode install
```

These commands update (or create) the skill files under `.claude/skills/` and
`AGENTS.md` respectively. No API key is needed for code-only extraction.

## Updating the fork

```bash
cd graphify-haxe
git pull
uv tool install --reinstall -e .
```

## Haxe support

This fork adds first-class AST extraction for:

- `.hx` files — classes, enums, abstracts, typedefs, interfaces, generics,
  metadata, modifiers, properties, arrow functions, switch/case, EMeta, type
  traces, conditionals, cross-file import resolution
- `.hxml` files — multi-target build config (`-lib`, `-cp`, `-main`, `-D`,
  `--interp`, `--next`)

Run graphify as normal — Haxe files are detected automatically:

```bash
graphify update .          # quick incremental rebuild
graphify extract .         # full re-extraction
```
