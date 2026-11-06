// vox call-agent secret guard: a pi extension that blocks a tool call before it runs.
//
// The call agent's replies are spoken aloud, so a secret it reads is a secret
// disclosed. pi starts executing a tool the instant it announces it, so nothing
// watching the RPC stream can stop a read; this `tool_call` handler runs inside
// pi, after the call is announced and before the tool executes, and can.
//
// For every path a read/grep/find/ls call names it does what pi itself does to
// the path (unicode spaces to plain, strip a leading "@", expand "~", decode a
// file:// URL, resolve against the session cwd), resolves symlinks, and requires
// the result to lie inside the repository root. Then it applies the secret
// deny-list patterns, case-insensitively. A grep `glob` or find `pattern` is
// checked against the patterns too. Any other tool is refused outright.
//
// A grep over a directory reads every file in it that ripgrep does not ignore,
// so before allowing one the guard lists exactly those files -- the same rg
// binary pi's grep uses, the same flags (`--hidden`, `--no-config`, the call's
// own `--glob`, so a negated glob widens the listing just as it widens the
// search) -- and blocks if any listed file fails the path check. A listing that
// fails, or no rg at all, blocks the grep: the guard cannot vouch for a search
// it cannot see.
//
// Configuration comes from vox through two environment variables:
//   VOX_CALL_GUARD_ROOT      the repository root
//   VOX_CALL_GUARD_PATTERNS  a JSON array of .gitignore-shaped patterns
// Missing or malformed configuration blocks every call: the guard fails closed.
// So does an exception in the handler, by pi's own contract.

import { spawnSync } from "node:child_process";
import { existsSync, realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PATH_TOOLS = new Set(["read", "grep", "find", "ls"]);
const UNICODE_SPACES = /[\u00A0\u2000-\u200A\u202F\u205F\u3000]/g;
const GLOB_CHARS = /[*?[\]{}!]/g;
export const REASON_PREFIX = "vox secret guard:";

// Mirror pi's normalizePath(..., { normalizeUnicodeSpaces, stripAtPrefix }).
export function normalize(input) {
  let p = String(input).replace(UNICODE_SPACES, " ");
  if (p.startsWith("@")) p = p.slice(1);
  if (p === "~") return homedir();
  if (p.startsWith("~/")) return path.join(homedir(), p.slice(2));
  if (/^file:\/\//.test(p)) return fileURLToPath(p);
  return p;
}

// realpath of the nearest existing ancestor, with the missing tail re-appended,
// so a path that does not exist yet still resolves through any symlinked parent.
export function realpathLoose(p) {
  const tail = [];
  let current = p;
  for (;;) {
    try {
      return path.join(realpathSync(current), ...tail);
    } catch {
      const parent = path.dirname(current);
      if (parent === current) return p;
      tail.unshift(path.basename(current));
      current = parent;
    }
  }
}

function globToRegex(glob) {
  let out = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*") {
      if (glob[i + 1] === "*") {
        out += ".*";
        i++;
        if (glob[i + 1] === "/") i++;
      } else {
        out += "[^/]*";
      }
    } else if (c === "?") {
      out += "[^/]";
    } else if (c === "[") {
      const end = glob.indexOf("]", i + 1);
      if (end === -1) {
        out += "\\[";
      } else {
        out += `[${glob.slice(i + 1, end).replace(/^!/, "^")}]`;
        i = end;
      }
    } else {
      out += c.replace(/[.+^${}()|\\]/g, "\\$&");
    }
  }
  // dotAll: `**` must cross a newline inside a filename, as `*` already does.
  return new RegExp(`^${out}$`, "is");
}

function compile(pattern) {
  const body = pattern.replace(/^\/+|\/+$/g, "");
  const regex = globToRegex(body);
  if (!body.includes("/")) {
    return (parts) => parts.some((part) => regex.test(part));
  }
  return (parts) =>
    parts.some((_, end) => regex.test(parts.slice(0, end + 1).join("/")));
}

// The rg pi's grep runs: pi's own tools dir first, then PATH (pi's getToolPath).
export function ripgrep() {
  const agentDir = process.env.PI_CODING_AGENT_DIR
    ? normalize(process.env.PI_CODING_AGENT_DIR)
    : path.join(homedir(), ".pi", "agent");
  const local = path.join(agentDir, "bin", "rg");
  return existsSync(local) ? local : "rg";
}

// The files `rg` would search under searchPath, with pi grep's ignore rules.
export function listSearchedFiles(searchPath, glob) {
  const args = ["--files", "--null", "--hidden", "--no-config"];
  if (glob) args.push("--glob", glob);
  args.push("--", searchPath);
  const result = spawnSync(ripgrep(), args, {
    encoding: "utf8",
    maxBuffer: 256 * 1024 * 1024,
    timeout: 15000,
  });
  if (result.error) throw new Error(result.error.message);
  if (result.status === 1 && !result.stdout) return [];
  if (result.status !== 0) throw new Error(`rg --files exited ${result.status}`);
  // NUL, not newline: a filename may contain "\n" but never "\0".
  return result.stdout.split("\0").filter(Boolean);
}

function isDirectory(p) {
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
}

export function createGuard({ root, patterns, listFiles = listSearchedFiles }) {
  if (typeof root !== "string" || !root) throw new Error("no repository root");
  if (!Array.isArray(patterns)) throw new Error("patterns is not a list");
  const realRoot = realpathSync(root);
  const matchers = patterns.map(compile);
  const denied = (parts) => matchers.some((matches) => matches(parts));

  const resolve = (raw, cwd) => {
    const normalized = normalize(raw);
    return path.isAbsolute(normalized)
      ? path.resolve(normalized)
      : path.resolve(normalize(cwd), normalized);
  };

  const checkPath = (raw, cwd) => {
    const absolute = resolve(raw, cwd);
    for (const candidate of [absolute, realpathLoose(absolute)]) {
      const rel = path.relative(realRoot, realpathLoose(candidate));
      if (rel === ".." || rel.startsWith(`..${path.sep}`) || path.isAbsolute(rel)) {
        return `${REASON_PREFIX} ${raw} is outside the repository`;
      }
      const lexical = path.relative(realRoot, candidate);
      for (const r of [rel, lexical]) {
        const parts = r ? r.split(path.sep) : [];
        if (denied(parts)) return `${REASON_PREFIX} ${raw} may hold secrets`;
      }
    }
    return "";
  };

  const checkGlob = (raw) => {
    const parts = String(raw).split("/").filter(Boolean);
    const stripped = parts.map((part) => part.replace(GLOB_CHARS, ""));
    return denied(parts) || denied(stripped)
      ? `${REASON_PREFIX} ${raw} may match files that hold secrets`
      : "";
  };

  const checkDirectoryGrep = (searchPath, glob) => {
    let files;
    try {
      files = listFiles(searchPath, glob);
    } catch (error) {
      return `${REASON_PREFIX} cannot list the files grep would read (${error.message})`;
    }
    for (const file of files) {
      const reason = checkPath(file, searchPath);
      if (reason) {
        const name = path.relative(searchPath, resolve(file, searchPath));
        return `${REASON_PREFIX} grep would read ${name}, which may hold secrets`;
      }
    }
    return "";
  };

  return (toolName, input, cwd) => {
    if (!PATH_TOOLS.has(toolName)) return `${REASON_PREFIX} ${toolName} is not allowed`;
    const args = input ?? {};
    const pathArg = typeof args.path === "string" && args.path ? args.path : ".";
    const reason = checkPath(pathArg, cwd);
    if (reason) return reason;
    for (const key of ["glob", "pattern"]) {
      if (key === "pattern" && toolName !== "find") continue;
      if (typeof args[key] === "string") {
        const globReason = checkGlob(args[key]);
        if (globReason) return globReason;
      }
    }
    const searchPath = resolve(pathArg, cwd);
    if (toolName === "grep" && isDirectory(searchPath)) {
      const glob = typeof args.glob === "string" && args.glob ? args.glob : "";
      return checkDirectoryGrep(searchPath, glob);
    }
    return "";
  };
}

function guardFromEnvironment() {
  try {
    return createGuard({
      root: process.env.VOX_CALL_GUARD_ROOT,
      patterns: JSON.parse(process.env.VOX_CALL_GUARD_PATTERNS ?? "null"),
    });
  } catch (error) {
    return () => `${REASON_PREFIX} misconfigured (${error.message}); every call is blocked`;
  }
}

export default function secretGuard(pi) {
  const guard = guardFromEnvironment();
  pi.on("tool_call", (event, ctx) => {
    const reason = guard(event.toolName, event.input, ctx?.cwd ?? process.cwd());
    return reason ? { block: true, reason, terminate: true } : undefined;
  });
}
