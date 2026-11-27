# Batch Segment Output Guide

`vox say --from segments.json` and `vox rec new --from segments.json` read a
JSON array of strings or objects with a string `text` field. Blank segments
are skipped. With `--json`, stdout is JSON Lines, not a JSON array: each
successful segment produces one JSON object per line, in input order.

## Failures

If the daemon rejects a segment or the connection fails during processing,
the command writes a final `{"error": "..."}` object to stdout, skips the
remaining segments, and exits nonzero. Earlier results stay in the output.
An invalid segments file instead produces a single `{"error": "..."}`
object and exits with status 2 before processing any segments. Successful
objects do not have an `error` key.

## Reading Partial Results

Save stdout before parsing so you can check Vox's exit status separately
and still read partial results. `jq -c .` reads the objects individually.
This is a script, not a single command; run it as a whole:

```sh
printf '%s\n' '["First line.", {"text":"Second line."}]' > segments.json
status=0
vox rec new --from segments.json --json > results.jsonl || status=$?
jq -c . results.jsonl
printf 'Vox exit status: %s\n' "$status" >&2
```

Use `vox say` in place of `vox rec new` to play the segments instead.
