# LaTeX templates

Phase 5 adds `resume.tex.j2` and `cover_letter.tex.j2` here.

They use Jinja2 with LaTeX-safe delimiters so `{ }` stays free for LaTeX:

| Purpose | Delimiter |
|---|---|
| Statement | `((* ... *))` |
| Expression | `((( ... )))` |
| Comment | `((= ... =))` |

All text from the resume YAML and the LLM is passed through a LaTeX escaping filter before rendering.
Compile with [tectonic](https://tectonic-typesetting.github.io/).
