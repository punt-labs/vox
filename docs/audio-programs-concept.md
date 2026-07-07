# Audio Programs — a unified multi-part audio concept (playlist / podcast / audiobook)

**Status:** High-level concept for review. Author: Claude (COO), 2026-07-05.
Not a design — the input to a design session. Bead: epic (see end).

## The idea in one sentence

Generalize the music playlist into one abstraction — a **Program**: a named,
addressable, ordered-or-rotating collection of **Parts**, each Part being
LLM-authored content + generated audio + metadata — with three **formats**
(playlist / podcast / audiobook) that differ only in which ElevenLabs engine
generates a Part, how Parts are ordered and played, and whether audio is a ducked
background or the foreground.

## Why one feature, not three

Playlist, podcast, and audiobook share the *entire* lifecycle and economics:

1. **Author** (LLM) → 2. **generate in the background** (ElevenLabs, costs credits)
→ 3. **store** as an ordered set of parts → 4. **play / advance / rotate**
(free — ElevenLabs charges for generation, not playback).

They also share the create/consume split, the daemon's playback queue, and the
"the LLM knows the content, vox is a pipe to ElevenLabs" principle we already
proved with music prompts. The differences are a clean **format strategy** axis,
not three separate subsystems.

## The Program model (fixes "it's a naming pattern, not a list" — vox-us4g)

- **Program**: `name`, `format` (playlist | podcast | audiobook), a subject
  (vibe+style for music; topic/brief for spoken), an ordered list of Parts, a
  **playback policy**, and a lifecycle state. First-class and persisted (a
  directory + manifest), so **CLI, MCP, and the daemon all address the same
  entity** instead of inferring a pool from filenames.
- **Part**: `index`, `title`, the authored spec (music prompt | dialogue turns |
  chapter text + voice casting), the generated audio file, `duration`, and
  `status` (pending | generating | ready | failed + reason).

The existing music internals generalize directly:
`TrackStore → PartStore`, `Playlist → Program` (with a playback policy),
`PoolFiller → Producer` (with a per-format generation backend). Reuse, not rewrite.

## The three formats

| Axis | **Playlist** (music) | **Podcast** (spoken series) | **Audiobook** (dramatic) |
|---|---|---|---|
| ElevenLabs engine | Music API `POST /v1/music` (`music_v2`, `force_instrumental`, 3s–10min, or a section `composition_plan`) | Text to Dialogue `POST /v1/text-to-dialogue` (eleven v3, multi-speaker turns, audio tags) | TTS long-form (`multilingual_v2`; Flash/Turbo up to 40k chars) + Dialogue for character lines + optional SFX/music bed |
| Part = | track | episode / segment | chapter |
| Order & lifecycle | shuffle-rotate, endless (fill to 12 then rotate) | sequential, finite N | sequential, finite chapters |
| Audio | background, **ducked**, loop | foreground | foreground (optional music/SFX bed) |
| Voices | none (instrumental) | cast: host + guest(s) | narrator + per-character cast |
| LLM authors | 12 genre prompts | a script (turns per speaker) | chapter text + voice casting |

Content is **orthogonal to format**:

- **Educational / project**: "teach me the ElevenLabs API", "walk me through
  PEP 8", "explain this repo's architecture" → a two-host **podcast** or a
  narrated **audiobook**; the LLM authors from its knowledge + the codebase
  (quarry/repo).
- **Entertainment**: "a 10-minute mystery loosely off my codebase" → a dramatic
  **audiobook** (multi-voice + SFX/music bed).
- **Ambient**: the music **playlist**.
- **Language learning**: content in a *target language* at a *target proficiency*
  (CEFR A1–C2) — e.g. "a B1-level German audiobook with a storyline about X", "an
  A2 Spanish podcast about my week." Applies to **podcast and audiobook**; the LLM
  authors the script/chapters directly in the target language at the requested
  level. ElevenLabs v3 and `multilingual_v2` cover 70+ languages, so it's a native
  fit. (Music is instrumental → not applicable.)

**Content dimensions** are orthogonal to format and are authoring inputs the LLM
receives per program: topic/brief, register (educational vs entertainment), and —
for spoken formats — **language + proficiency level**. Persistence is not a
dimension: **every Program is saved to disk (a named directory + manifest) and is
replayable** — from the user's point of view programs are permanent, not
throwaway. The contrast with Studio (below) is about *who authors and where
playback lives*, never about whether ours are saved.

## The create/consume split (the operator's constraint, and it maps to ElevenLabs)

- **Claude Code (MCP) = creative + consumption.** The LLM is the *showrunner*:
  it authors music prompts / podcast scripts / audiobook chapters + casting,
  calls vox to generate and play. Content knowledge lives here (same reason
  music prompts must be LLM-authored — no separate content API key, and only the
  model knows what the content should be). Generation costs credits.
- **CLI = consumption only.** No LLM ⇒ no authoring, no generation. It lists,
  plays, advances, and rotates **existing** Programs — which is *free* on
  ElevenLabs (playback of generated audio consumes no credits). `vox program list`,
  `vox program play <name>`, `vox program next`, `vox program loop <name>`. Until
  the CLI gets its own content-generation path (more API keys + an authoring
  model), it is a player for what Claude Code produced.

This is why the split is natural rather than a limitation: ElevenLabs already
separates paid generation from free playback; vox's two modalities land exactly
on that seam.

## Ties to work already in flight

- **vox-us4g** (CLI has no first-class playlist) is subsumed: the Program model
  is the "explicit list", and consume-only CLI verbs are the playlist half of it.
- **vox-ig52** (client-observable failure) generalizes: a podcast episode or
  chapter that fails to generate must surface via `status` (`part.status =
  failed` + reason), never vanish. The observability contract is the same.
- **h7h5** (LLM-authored music prompts) is the template: the LLM authors, vox
  pipes to ElevenLabs, the daemon plays. Podcast scripts and audiobook chapters
  are the same pattern with a different engine.

## Open design decisions (for the design session)

1. **Entry points.** One `program` tool/verb, or ergonomic per-format commands
   (`/music`, `/podcast`, `/audiobook`) that all produce Programs? (Lean:
   per-format commands, one shared Program domain model underneath.)
2. **Simultaneous playback.** Can a spoken Program run in the foreground while a
   music Program plays a ducked bed (audiobook-with-score)? Compelling, but adds
   a mixing concern.
3. **Persistence & manifest.** Program = a directory with a manifest (parts +
   metadata + casting). Define the minimal manifest the CLI needs to play/advance
   without the daemon regenerating.
4. **Length & credit budgeting.** Podcasts/audiobooks are long (many paid
   minutes). Need length caps + a cost confirmation before generation; playback
   stays free.
5. **Cast management.** How speaker/character → `voice_id` assignment works; use
   IVC/designed voices for v3 (PVC not yet optimized for v3).
6. **Engine choice per format.** Direct-to-API (compose / text-to-dialogue / TTS)
   and own the Program model in the daemon — lighter and a better daemon fit than
   driving ElevenLabs Studio "Projects" as the backend. Confirm.
7. **Formal model.** The Program lifecycle (author → generating → ready → playing
   → advancing/rotating → failed, per format) is a state machine — z-spec it
   before implementation, same trigger as vox-ig52.

## Decisions (operator ruling, 2026-07-05)

1. **Per-format commands, per-format LLM instructions.** `/music`, `/podcast`,
   `/audiobook` each carry their *own* authoring instructions to the LLM (as
   `commands/music.md` does today), independently fine-tunable. One shared
   Program model underneath.
2. **No cross-program mixing.** Background effects/music are baked *into* the
   audiobook (or podcast) Program at generation time — one Program, bed embedded
   — not a separate music Program ducked underneath. This drops the
   simultaneous-playback/mixing concern entirely. Simpler.
3. **CLI addresses Programs *and* Parts.** Consume-only, but it can `list` a
   program, `play` a program, and select a specific part in series — e.g.
   `vox music playlist playlist:2` (part 2). A Part = track / chapter / episode
   by index within the Program.
4. **Length/credit budgeting** — deferred; not now.
5. **Cast management** — deferred; not now.
6. **Direct-to-API** (not ElevenLabs Studio "Projects" as backend). Keep podcasts
   and audiobooks deliberately on the *simpler* side.
7. **Model first (z-spec), and fix lengths:**
   - **Music**: vary track length realistically + slightly randomized. Today
     every track is exactly 2m (`music_length_ms = 120000`) — unnatural. Should
     span a realistic range. (Near-term quick win, independent of the epic.)
   - **Podcast**: ~5–10 min per episode.
   - **Audiobook**: ~5 min chapters; a "book" up to ~30 min total (≈6 chapters).

These supersede the corresponding open questions above.

### Why not ElevenLabs Studio as the backend (decided 2026-07-05)

Studio (formerly "Projects") is ElevenLabs' timeline **editor + stateful project**
workflow for long-form audio (chapters, paragraph-level generation, multi-voice
casting, in-timeline music/SFX beds, pronunciation dictionaries, selective
regeneration, publish/distribute). It is built for **humans producing a polished,
distributable book in an editor**. Our Programs are **agent-authored and played
through voxd** (and saved on our own disk) — a different shape. Decision: go
**direct-to-API** (Music / Text-to-Dialogue / TTS) and own the Program model.

- Direct-to-API keeps podcast/audiobook on the same rails as the playlist (Phase
  1): our store, our daemon, "generate a part → play it." A podcast episode is one
  Text-to-Dialogue call; an audiobook chapter is chunked TTS.
- Studio's real strengths (editor, cheap selective regen, publishing, distribution)
  are mostly irrelevant to "generate a 10-min program and play it," and its API has
  real limits (thin endpoint docs, **SFX not supported when streaming via the
  Studio API**, plan-gated quality). The two things worth borrowing — chapter
  structure and free playback — the Program model already gives us.
- **Studio stays in the back pocket** for a *future, different* capability: "export
  this program as a real, distributable, human-editable audiobook." That is a
  publish/export path, not the core Programs feature.

## Phasing (operator, 2026-07-05 — Phase 1 first)

**Phase 1 — move today's music onto the Program model + unlock playlist replay
(CLI + MCP).** No new ElevenLabs engines; refactor + one new capability.

- Generalize the existing music internals into the shared model, **`playlist`
  format only**: `TrackStore → PartStore`, `Playlist → Program`,
  `PoolFiller → Producer` (music engine). A Program becomes a first-class, named,
  **persisted** entity (directory + manifest) — not a filename pattern.
  **Subsumes vox-us4g.**
- Keep the existing MCP `music` authoring path (the LLM authors prompts) — it now
  *produces a Program* instead of loose files.
- **New capability:** CLI **and** MCP can `list` programs, `play` a program,
  `loop`/rotate it, and select a specific part (`vox music playlist playlist:2`)
  — consume-only on the CLI (free playback, no LLM).
- This proves the Program model, the manifest, the CLI part-addressing, and the
  status-observability contract on the format that already exists — de-risking
  Phases 2–3.
- **Fold in / coordinate:** vox-y3om (varied music length — we're in the
  generation path anyway) and vox-ig52's observability contract (`part.status`
  surfaced via `status`), since both refactor the same music path. Sequence so
  they don't collide.

**Phase 2 — Podcast.** Text-to-Dialogue engine, multi-speaker, 5–10m episodes;
`/podcast` with its own authoring instructions. Slots into the Program frame.

**Phase 3 — Audiobook.** TTS long-form, ~5m chapters / ~30m books (beds a
fast-follow); `/audiobook` with its own authoring instructions.

## Sources

- ElevenLabs Music API — compose, `music_length_ms` (3s–10min), `force_instrumental`,
  composition plans (free), `music_v2`.
- ElevenLabs Text to Dialogue (eleven v3) — multi-speaker turns, audio tags,
  ~3k char/render, not for real-time; the podcast/character engine.
- ElevenLabs Studio / Audiobooks — chapters, paragraph-level generation, multi-voice,
  music/SFX beds, **playback of generated audio is free**, selective regeneration.
- ElevenLabs TTS long-form — `multilingual_v2` (quality), Flash/Turbo (up to 40k chars).
