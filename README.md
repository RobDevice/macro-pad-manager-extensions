# Macro Pad Manager Extensions

Official extension catalogue and downloadable artifacts for Macro Pad Manager.

This repository and its first-party extensions are licensed under
**GPLv3 only**. See [`LICENSE`](LICENSE) for the full license text.

## Layout

- `catalogue.json`
  - extension index consumed by the app
- `artifacts/`
  - ZIP files referenced by `catalogue.json`
- `extensions/`
  - unpacked extension source trees used to build the published artifacts

## Related Repositories

- App repository: `https://github.com/RobDevice/macro-pad-manager`
- Extensions repository: `https://github.com/RobDevice/macro-pad-manager-extensions`

## Publishing Notes

- Keep `catalogue.json` and `artifacts/` in sync.
- Prefer immutable artifact filenames that include the extension version.
- Validate catalogue changes with your publishing tooling before pushing.
