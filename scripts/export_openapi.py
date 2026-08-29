"""Write the API's OpenAPI schema to a file, without starting the server.

    just openapi

The frontend's TypeScript types are generated from this document, so it is the
contract between the two halves of the repo. Exporting it from the FastAPI app
directly — rather than curling a running `just dev` — matters for two reasons:
CI has no server to curl, and a schema fetched from whatever happens to be
running is a schema nobody can tell apart from a stale one.

The app is built with `expose_local_only_routes=False` on purpose. That is the
production surface, and the frontend must never learn that
`POST /ingestion/bank-notifications` exists: it is a local testing seam, and a
generated client that offers it would be inviting the one call the guide
forbids.

The output is sorted and newline-terminated so that regenerating it produces
an empty diff when nothing changed, and a reviewable one when something did.
An endpoint added without running this shows up as a dirty working tree.

Importing `personal_finance.api.main` also builds that module's own `app` as a
side effect, shaped by whatever `ENV_FILE` says — under `.env` that one *does*
mount the webhook. It is discarded and never serialized; the document below
comes from the app built here. That is also why the recipe requires `.env`
like its siblings: the import needs settings that validate.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from personal_finance.api.main import create_app


DEFAULT_OUTPUT = pathlib.Path("docs/openapi.json")


def build_schema() -> dict[str, Any]:
    """The production-shaped schema, with the local-only webhook left out."""
    app = create_app(expose_local_only_routes=False)
    return app.openapi()


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        nargs="?",
        type=pathlib.Path,
        default=DEFAULT_OUTPUT,
        help=f"where to write the schema (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the file on disk is not what this would write",
    )
    args = parser.parse_args(argv)

    rendered = render(build_schema())

    if args.check:
        if not args.output.exists():
            print(f"{args.output} does not exist — run `just openapi`", file=sys.stderr)
            return 1
        if args.output.read_text(encoding="utf-8") != rendered:
            print(
                f"{args.output} is stale — run `just openapi` and commit the result",
                file=sys.stderr,
            )
            return 1
        print(f"{args.output} is up to date")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    paths = len(json.loads(rendered)["paths"])
    print(f"wrote {args.output} ({paths} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
