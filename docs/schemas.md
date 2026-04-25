---
title: JSON Schemas
nav_order: 3
permalink: /schemas/
---

<!-- Auto-synced from blemees/schemas/README.md by .github/workflows/docs-sync.yml. -->

# blemees — wire-frame JSON Schemas

Machine-readable contract for every frame on the `blemees/1` protocol.
The prose spec is the repository root `README.md`; the schemas in this
directory formalize the frame shapes referenced there.

These ship inside the `blemees` wheel as the `blemees.schemas`
subpackage, so installed clients can validate frames without copying
JSON anywhere:

```python
from blemees.schemas import load, iter_schemas, files

hello = load("inbound/blemeesd.hello.json")   # parsed dict
all_frames = list(iter_schemas())             # every shipped schema
root = files()                                # importlib.resources Traversable
```

## Layout

```
blemees/schemas/
  _common.json               # shared $defs (SessionId, Seq, MessageContent, …)
  inbound/                   # client → daemon frames
    blemeesd.hello.json
    blemeesd.open.json
    blemeesd.interrupt.json
    blemeesd.close.json
    blemeesd.list_sessions.json
    blemeesd.ping.json
    blemeesd.status.json
    blemeesd.watch.json
    blemeesd.unwatch.json
    blemeesd.session_info.json
    claude.user.json
  outbound/                  # daemon → client frames
    blemeesd.hello_ack.json
    blemeesd.opened.json
    blemeesd.closed.json
    blemeesd.interrupted.json
    blemeesd.error.json
    blemeesd.stderr.json
    blemeesd.replay_gap.json
    blemeesd.sessions.json
    blemeesd.session_taken.json
    blemeesd.pong.json
    blemeesd.status_reply.json
    blemeesd.watching.json
    blemeesd.unwatched.json
    blemeesd.session_info_reply.json
    claude.event.json        # envelope for every forwarded CC event
```

## Draft / compatibility rules

* **Draft**: JSON Schema `2020-12` (via `$schema`).
* **Inbound frames** are strict (`additionalProperties: false`) — the
  daemon rejects unknown fields with `invalid_message`. Fields the
  daemon owns (`input_format`, `output_format`) and the legacy unsafe
  flags (`dangerously_skip_permissions`, …) are refused explicitly via
  a `not` clause on `blemeesd.open`.
* **Outbound frames** permit `additionalProperties: true` so the daemon
  can grow the envelope (e.g. new debug fields) without breaking
  conforming clients.
* **`claude.*` events** use a loose envelope
  (`schemas/outbound/claude.event.json`) — only `type`, `session_id`, and
  `seq` are constrained; the inner CC payload (`message`, `event`,
  `result`, …) is not validated here, because Claude Code owns that
  schema and we are pass-through.

## Use

Validate a frame with any JSON Schema 2020-12 library. From an
installed `blemees` wheel:

```python
from blemees.schemas import iter_schemas
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

# Build a registry once so $refs (e.g. into _common.json) resolve.
store = {s["$id"]: s for s in iter_schemas()}
registry = Registry()
for uri, schema in store.items():
    registry = registry.with_resource(uri, Resource.from_contents(schema))

def validate(frame_type: str, frame: dict, direction: str = "inbound") -> None:
    url = f"https://blemees/schemas/{direction}/{frame_type}.json"
    Draft202012Validator(store[url], registry=registry).validate(frame)
```

If you need on-disk paths (for tooling that does not understand
`importlib.resources`), use `as_file`:

```python
from importlib.resources import as_file
from blemees.schemas import files

with as_file(files() / "inbound" / "blemeesd.hello.json") as p:
    print(p)   # real filesystem path you can hand to a generator
```

Generators (`datamodel-code-generator`, `quicktype`, etc.) can turn
these schemas into typed models in most languages.

## Versioning

Breaking changes to any frame shape bump the protocol version (`blemees/1`
→ `blemees/2`); the daemon rejects old versions on `blemeesd.hello`
with `code: protocol_mismatch`. Additive, backward-compatible changes
stay on the same version.
