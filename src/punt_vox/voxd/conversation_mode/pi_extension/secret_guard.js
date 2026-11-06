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
// deny-list patterns, case-insensitively, and -- in a git work tree -- asks git
// whether it ignores the path: every ignore source git honours (a .gitignore at
// any depth, .git/info/exclude, core.excludesFile), with git's own scoping,
// negation, and anchoring. A git that cannot answer refuses the call. A grep
// `glob` or find `pattern` is checked against the patterns too. Any other tool
// is refused outright.
//
// A grep over a directory reads every file in it that ripgrep does not ignore,
// so before allowing one the guard lists exactly those files -- the same rg
// binary pi's grep uses, the same flags (`--hidden`, `--no-config`, the call's
// own `--glob`, so a negated glob widens the listing just as it widens the
// search) -- and blocks if any listed file fails the path check. A listing that
// fails, or no rg at all, blocks the grep: the guard cannot vouch for a search
// it cannot see.
//
// Configuration comes from vox through three environment variables:
//   VOX_CALL_GUARD_ROOT         the repository root
//   VOX_CALL_GUARD_PATTERNS     a JSON array of .gitignore-shaped patterns
//   VOX_CALL_GUARD_GIT_IGNORES  JSON true when the root is in a git work tree
// Missing or malformed configuration fails the extension's load, and pi exits
// at startup: the guard fails closed by never letting the agent start. An
// exception in the handler blocks that call, by pi's own contract.

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

// Those of `paths` (relative to `root`, "/"-separated) that git ignores, asked
// of git itself. --no-index: a tracked file a rule matches counts as ignored
// too, the safe side. Throws when git does not answer.
export function gitIgnored(root, paths) {
  if (paths.length === 0) return new Set();
  const result = spawnSync("git", ["check-ignore", "--no-index", "-z", "--stdin"], {
    cwd: root,
    input: `${paths.join("\0")}\0`,
    encoding: "utf8",
    maxBuffer: 256 * 1024 * 1024,
    timeout: 15000,
  });
  if (result.error) throw new Error(result.error.message);
  // 0: some paths are ignored; 1: none are; anything else: no answer.
  if (result.status !== 0 && result.status !== 1) {
    throw new Error(`git check-ignore exited ${result.status}: ${result.stderr.trim()}`);
  }
  return new Set(result.stdout.split("\0").filter(Boolean));
}

function isDirectory(p) {
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
}

export function createGuard({
  root,
  patterns,
  gitIgnores,
  listFiles = listSearchedFiles,
  checkIgnored = gitIgnored,
}) {
  if (typeof root !== "string" || !root) throw new Error("no repository root");
  if (!Array.isArray(patterns)) throw new Error("patterns is not a list");
  if (typeof gitIgnores !== "boolean") throw new Error("gitIgnores is not a boolean");
  const realRoot = realpathSync(root);
  const matchers = patterns.map(compile);
  const denied = (parts) => matchers.some((matches) => matches(parts));

  const resolve = (raw, cwd) => {
    const normalized = normalize(raw);
    return path.isAbsolute(normalized)
      ? path.resolve(normalized)
      : path.resolve(normalize(cwd), normalized);
  };

  const outside = (rel) =>
    rel === ".." || rel.startsWith(`..${path.sep}`) || path.isAbsolute(rel);

  // A path relative to the root, symlinks resolved, then as named.
  const relativeForms = (raw, cwd) => {
    const absolute = resolve(raw, cwd);
    return [
      path.relative(realRoot, realpathLoose(absolute)),
      path.relative(realRoot, absolute),
    ];
  };

  const checkPatterns = (raw, cwd) => {
    const forms = relativeForms(raw, cwd);
    if (outside(forms[0])) return `${REASON_PREFIX} ${raw} is outside the repository`;
    const secret = forms.some((rel) => denied(rel ? rel.split(path.sep) : []));
    return secret ? `${REASON_PREFIX} ${raw} may hold secrets` : "";
  };

  // The first of `paths` ([raw, cwd] pairs) git ignores, "" for none; one git
  // call for the lot. Throws when git does not answer.
  const firstIgnored = (paths) => {
    if (!gitIgnores) return "";
    const asked = paths.map(([raw, cwd]) => [
      raw,
      relativeForms(raw, cwd)
        .filter((rel) => rel && !outside(rel))
        .map((rel) => rel.split(path.sep).join("/")),
    ]);
    const rels = [...new Set(asked.flatMap(([, forms]) => forms))];
    const ignored = checkIgnored(realRoot, rels);
    const hit = asked.find(([, rels]) => rels.some((rel) => ignored.has(rel)));
    return hit ? hit[0] : "";
  };

  const checkIgnores = (paths, describe) => {
    let ignored;
    try {
      ignored = firstIgnored(paths);
    } catch (error) {
      return `${REASON_PREFIX} cannot check what git ignores (${error.message})`;
    }
    return ignored ? describe(ignored) : "";
  };

  const checkPath = (raw, cwd) =>
    checkPatterns(raw, cwd) ||
    checkIgnores(
      [[raw, cwd]],
      (name) => `${REASON_PREFIX} ${name} is ignored by git and may hold secrets`,
    );

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
    const readName = (file) => path.relative(searchPath, resolve(file, searchPath));
    for (const file of files) {
      if (checkPatterns(file, searchPath)) {
        return `${REASON_PREFIX} grep would read ${readName(file)}, which may hold secrets`;
      }
    }
    return checkIgnores(
      files.map((file) => [file, searchPath]),
      (file) => `${REASON_PREFIX} grep would read ${readName(file)}, which git ignores`,
    );
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

// Throws on missing or malformed configuration, and pi refuses to start when an
// extension fails to load -- so a misconfigured guard fails the call at start,
// loudly, rather than leaving an agent that refuses every turn.
function guardFromEnvironment() {
  try {
    return createGuard({
      root: process.env.VOX_CALL_GUARD_ROOT,
      patterns: JSON.parse(process.env.VOX_CALL_GUARD_PATTERNS ?? "null"),
      gitIgnores: JSON.parse(process.env.VOX_CALL_GUARD_GIT_IGNORES ?? "null"),
    });
  } catch (error) {
    throw new Error(`${REASON_PREFIX} misconfigured: ${error.message}`);
  }
}

export default function secretGuard(pi) {
  const guard = guardFromEnvironment();
  pi.on("tool_call", (event, ctx) => {
    const reason = guard(event.toolName, event.input, ctx?.cwd ?? process.cwd());
    return reason ? { block: true, reason, terminate: true } : undefined;
  });
}
