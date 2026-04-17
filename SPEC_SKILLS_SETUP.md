# Spec Skills Setup

This plugin now includes a local vendored copy of Spec Kit at:

`third_party/spec-kit`

Notes:

- `spec_skills` still exists as a junction that points to `K:\Codeing\System Prompt\spec-kit-main\spec-kit-main`
- `third_party/spec-kit` is the actual in-project copy that can be used even if the external `K:` path is unavailable
- `.specify/` already contains the active templates and scripts used by the current project workflow
- `.kilo/skills/` already contains project-facing Speckit skills for day-to-day use

Recommended usage inside this project:

- Use `.kilo/skills/speckit-*` for direct agent skill flows
- Use `.specify/templates` and `.specify/scripts` for spec generation workflow
- Use `third_party/spec-kit` as the local reference source for docs, extensions, templates, and upstream updates

If we want later, we can replace the `spec_skills` junction with a normal directory copy after cleaning the existing reparse-point safely.
