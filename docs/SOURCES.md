# Official Technical Sources

Verified: 2026-08-12

These sources ground the initial architecture and pinned framework families. Re-check the relevant page before upgrading or implementing version-sensitive behavior.

## Python and FastAPI

- FastAPI first steps, application creation, OpenAPI, and entrypoint guidance: <https://fastapi.tiangolo.com/tutorial/first-steps/>
- Python Packaging User Guide for `pyproject.toml`: <https://packaging.python.org/en/latest/guides/writing-pyproject-toml/>
- uv project and lockfile workflow: <https://docs.astral.sh/uv/guides/projects/>
- FastAPI `UploadFile` multipart handling and spooled-file behavior: <https://fastapi.tiangolo.com/tutorial/request-files/>
- FastAPI request-scoped dependencies with `yield`: <https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/>
- FastAPI lifespan initialization and cleanup: <https://fastapi.tiangolo.com/advanced/events/>
- FastAPI SQLite thread guidance: <https://fastapi.tiangolo.com/tutorial/sql-databases/>

## React and Vite

- React installation and current guidance against Create React App: <https://react.dev/learn/installation>
- Vite 8 release and Node.js support: <https://vite.dev/blog/announcing-vite8>
- Vite getting started: <https://vite.dev/guide/>
- React controlled input and file input reference: <https://react.dev/reference/react-dom/components/input>
- Browser upload progress events: <https://developer.mozilla.org/en-US/docs/Web/API/XMLHttpRequestEventTarget/progress_event>

## Agent and structured model use

- OpenAI current model catalog: <https://developers.openai.com/api/docs/models>
- OpenAI model guidance for bounded tool workflows, schemas, retry/stop limits, and approval-sensitive direct calls: <https://developers.openai.com/api/docs/guides/latest-model>
- Ollama native chat request, `format`, `stream`, and `think` fields: <https://docs.ollama.com/api/chat>
- Ollama structured outputs and JSON Schema guidance: <https://docs.ollama.com/capabilities/structured-outputs>
- Ollama thinking controls: <https://docs.ollama.com/capabilities/thinking>

## Storage, OCR, calendar, and credentials

- Python 3.12 `pathlib` resolution and path containment primitives: <https://docs.python.org/3.12/library/pathlib.html#pathlib.Path.resolve>
- Python 3.12 incremental SHA-256 hashing: <https://docs.python.org/3.12/library/hashlib.html#usage>
- Python 3.12 secure same-directory temporary-file creation: <https://docs.python.org/3.12/library/tempfile.html#tempfile.mkstemp>
- Python 3.12 hard-link creation on Unix and Windows: <https://docs.python.org/3.12/library/os.html#os.link>
- Python 3.12 filename-based media-type mapping and its limits: <https://docs.python.org/3.12/library/mimetypes.html#mimetypes.guess_type>
- Python strict UTF-8-with-BOM decoding (`utf-8-sig`): <https://docs.python.org/3.12/library/codecs.html#encodings.utf_8_sig>
- Python email byte parsing: <https://docs.python.org/3.12/library/email.parser.html>
- Python email message traversal and attachment semantics: <https://docs.python.org/3.12/library/email.message.html>
- Python spawned process and termination primitives: <https://docs.python.org/3.12/library/multiprocessing.html>
- Python `zoneinfo` and its system/first-party `tzdata` data-source fallback: <https://docs.python.org/3/library/zoneinfo.html>
- tzdata 2026.3 package metadata, Python compatibility, and Apache-2.0 license: <https://pypi.org/project/tzdata/2026.3/>
- pypdf 6.14.2 package metadata, Python compatibility, and BSD-3-Clause license expression: <https://pypi.org/project/pypdf/6.14.2/>
- pypdf text extraction, memory caveat, and explicit non-OCR boundary: <https://pypdf.readthedocs.io/en/latest/user/extract-text.html>
- pypdf 6.14.2 license text: <https://github.com/py-pdf/pypdf/blob/6.14.2/LICENSE>
- SQLite foreign-key activation and per-connection behavior: <https://www.sqlite.org/foreignkeys.html>
- SQLite transaction control and `BEGIN IMMEDIATE`: <https://www.sqlite.org/lang_transaction.html>
- SQLite PRAGMA reference for foreign keys, WAL journal mode, and busy timeout: <https://www.sqlite.org/pragma.html>
- Python `sqlite3` connection and transaction control: <https://docs.python.org/3/library/sqlite3.html>
- SQLite FTS5: <https://www.sqlite.org/fts5.html>
- Tesseract documentation: <https://tesseract-ocr.github.io/>
- Tesseract input formats and PDF limitation: <https://tesseract-ocr.github.io/tessdoc/InputFormats.html>
- iCalendar RFC 5545: <https://www.rfc-editor.org/info/rfc5545/>
- Windows Credential Locker: <https://learn.microsoft.com/en-us/windows/apps/develop/security/credential-locker>

## Optional isolation

- Docker resource constraints: <https://docs.docker.com/engine/containers/resource_constraints/>
- Docker `none` network driver: <https://docs.docker.com/engine/network/drivers/none/>

## GitHub Actions

- Checkout action: <https://github.com/actions/checkout>
- Node setup action: <https://github.com/actions/setup-node>
- Official uv setup action: <https://github.com/astral-sh/setup-uv>
