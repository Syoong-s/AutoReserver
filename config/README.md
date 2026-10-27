# Local configuration

The default application writes its private settings to `config/config.json` in
this program directory, independent of the directory from which it is launched.
Do not commit that file or generated evidence. `example.json` is public, empty
sample data. On first launch an older root-level `config.json` is validated,
migrated here, and retained as `legacy-config.json` as a private backup. Existing
new settings are never replaced by the old file.

Authentication and the durable order journal remain in the application-local
`.auth/` folder so existing login and duplicate-order protections are preserved.

`config.json` also stores the boolean `initialized`. First-run setup marks it true
only after real runtime/browser verification. `bootstrap.json` is a private setup
receipt; neither file is published. Public `example.json` always starts false.
See ../docs/FIRST_RUN.md for local-only dependency installation and retry behavior.
