#!/usr/bin/env bash

# Print one top-level string field of the hook's JSON payload on stdin,
# decoded, or nothing when the field is absent, null, or not a string.
# jq is optional, so the fallback is a small JSON scanner in POSIX awk:
# grep cannot see past an escaped quote, and only a decoder turns \" \\
# and \uXXXX back into the bytes of the path.
vox_hook_payload_field() {
  if command -v jq >/dev/null 2>&1; then
    jq -j --arg key "$1" '.[$key] // empty' 2>/dev/null
    return
  fi
  # LC_ALL=C keeps awk byte-oriented: raw UTF-8 passes through untouched
  # and %c emits exactly one byte for each UTF-8 byte of a \u escape.
  LC_ALL=C awk -v key="$1" '
    function fail() { exit 1 }

    function hex4(pos,   k, d, value) {
      value = 0
      for (k = 0; k < 4; k++) {
        d = index("0123456789abcdef", tolower(substr(json, pos + k, 1)))
        if (d == 0) return -1
        value = value * 16 + d - 1
      }
      return value
    }

    function utf8(cp) {
      if (cp < 128) return sprintf("%c", cp)
      if (cp < 2048)
        return sprintf("%c%c", 192 + int(cp / 64), 128 + cp % 64)
      if (cp < 65536)
        return sprintf("%c%c%c", 224 + int(cp / 4096),
          128 + int(cp / 64) % 64, 128 + cp % 64)
      return sprintf("%c%c%c%c", 240 + int(cp / 262144),
        128 + int(cp / 4096) % 64, 128 + int(cp / 64) % 64, 128 + cp % 64)
    }

    # Decode the JSON string whose opening quote is at i; leave i just
    # past its closing quote. Unescaped runs are copied whole, so a long
    # string costs one pass however many escapes it holds.
    function string_at(   out, start, c, e, cp, lo) {
      out = ""
      start = ++i
      while (i <= n) {
        c = substr(json, i, 1)
        if (c != "\"" && c != "\\") { i++; continue }
        out = out substr(json, start, i - start)
        i++
        if (c == "\"") return out
        e = substr(json, i, 1)
        i++
        if (e == "\"" || e == "\\" || e == "/") out = out e
        else if (e == "b") out = out "\b"
        else if (e == "f") out = out "\f"
        else if (e == "n") out = out "\n"
        else if (e == "r") out = out "\r"
        else if (e == "t") out = out "\t"
        else if (e == "u") {
          cp = hex4(i)
          if (cp < 0) fail()
          i += 4
          if (cp >= 55296 && cp < 56320 && substr(json, i, 2) == "\\u" &&
              (lo = hex4(i + 2)) >= 56320 && lo < 57344) {
            cp = 65536 + (cp - 55296) * 1024 + (lo - 56320)
            i += 6
          } else if (cp >= 55296 && cp < 57344) {
            cp = 65533
          }
          out = out utf8(cp)
        } else fail()
        start = i
      }
      fail()
    }

    # Step i past the JSON string opening at i without decoding it.
    function skip_string(   c) {
      for (i++; i <= n; i++) {
        c = substr(json, i, 1)
        if (c == "\\") i++
        else if (c == "\"") { i++; return }
      }
      fail()
    }

    function skip_space() {
      while (i <= n && index(" \t\r\n", substr(json, i, 1)) > 0) i++
    }

    { json = json $0 "\n" }

    END {
      n = length(json)
      i = 1
      skip_space()
      if (substr(json, i, 1) != "{") fail()
      depth = 0
      prev = ""
      value = ""
      while (i <= n) {
        c = substr(json, i, 1)
        if (c == "\"") {
          if (depth != 1 || (prev != "{" && prev != ",")) skip_string()
          else if (string_at() == key) {
            skip_space()
            if (substr(json, i, 1) != ":") fail()
            i++
            skip_space()
            value = substr(json, i, 1) == "\"" ? string_at() : ""
          }
          prev = "\""
          continue
        }
        if (c == "{" || c == "[") depth++
        else if (c == "}" || c == "]") depth--
        if (index("{}[],:", c) > 0) prev = c
        else if (index(" \t\r\n", c) == 0) prev = "v"
        i++
      }
      printf "%s", value
    }
  '
}
