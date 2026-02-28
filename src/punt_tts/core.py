"""Core synthesis orchestration — batching, pair stitching, merging."""

from __future__ import annotations

import logging
import re
import tempfile
import warnings
from pathlib import Path
from typing import Any

from punt_tts.types import (
    MergeStrategy,
    SynthesisRequest,
    SynthesisResult,
    TTSProvider,
    generate_filename,
)

# pydub 0.25.1 uses unescaped parentheses in regex strings (utils.py lines
# 300, 301, 310, 314) which emit SyntaxWarning on Python 3.13 during bytecode
# compilation. This is an upstream bug; filter before importing pydub.
# Must match on message, not module — compile-time warnings ignore module filters.
warnings.filterwarnings(
    "ignore", category=SyntaxWarning, message=r"invalid escape sequence"
)
from pydub import AudioSegment  # noqa: E402

logger = logging.getLogger(__name__)

__all__ = ["TTSClient", "split_text", "stitch_audio"]

# Trailing silence appended to every final output file to prevent
# MP3 frame truncation / player cutoff at end of audio.
TRAILING_SILENCE_MS = 150


def _pad_audio_file(path: Path) -> None:
    """Append trailing silence to an MP3 file to prevent end-of-audio clipping."""
    audio: Any = AudioSegment.from_mp3(str(path))  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
    tail: Any = AudioSegment.silent(duration=TRAILING_SILENCE_MS)
    padded: Any = audio + tail  # pyright: ignore[reportUnknownVariableType]
    padded.export(str(path), format="mp3")  # pyright: ignore[reportUnknownMemberType]


# Sentence-ending punctuation followed by a space.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def split_text(text: str, max_chars: int) -> list[str]:
    """Split text into chunks that fit within the character limit.

    Every returned chunk is guaranteed ``<= max_chars``.

    Strategy:
    1. If the text fits, return it as-is.
    2. Split at sentence boundaries (after . ! ? followed by whitespace).
    3. Accumulate sentences into chunks up to max_chars.
    4. If a single sentence exceeds max_chars, split at word boundaries.

    Note: whitespace between sentences is normalized to a single space
    when two sentences are accumulated into the same chunk. This is
    intentional — TTS engines treat all whitespace identically.
    """
    if len(text) <= max_chars:
        return [text]

    sentences = _SENTENCE_SPLIT_RE.split(text)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if not sentence:
            continue

        # If a single sentence exceeds the limit, split at word boundaries.
        if len(sentence) > max_chars:
            # Flush current buffer first.
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_at_words(sentence, max_chars))
            continue

        candidate = f"{current} {sentence}" if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = sentence

    if current:
        chunks.append(current)

    return chunks


def _split_at_words(text: str, max_chars: int) -> list[str]:
    """Split text at word boundaries to fit within max_chars.

    Words exceeding max_chars are split into fixed-size character chunks
    so every returned piece is guaranteed ``<= max_chars``.
    """
    words = text.split()
    chunks: list[str] = []
    current = ""

    for word in words:
        if len(word) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(word), max_chars):
                chunks.append(word[i : i + max_chars])
            continue

        candidate = f"{current} {word}" if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = word

    if current:
        chunks.append(current)

    return chunks


class TTSClient:
    """Provider-agnostic orchestrator for TTS operations.

    Delegates individual synthesis calls to the given TTSProvider and
    handles batching, pair stitching, and merging.
    """

    def __init__(self, provider: TTSProvider) -> None:
        self._provider = provider

    def synthesize(
        self, request: SynthesisRequest, output_path: Path
    ) -> SynthesisResult:
        """Synthesize a single text to an audio file."""
        result = self._provider.synthesize(request, output_path)
        _pad_audio_file(output_path)
        return result

    def synthesize_batch(
        self,
        requests: list[SynthesisRequest],
        output_dir: Path,
        merge_strategy: MergeStrategy = MergeStrategy.ONE_FILE_PER_INPUT,
        pause_ms: int = 500,
    ) -> list[SynthesisResult]:
        """Synthesize multiple texts, optionally merging into one file.

        Args:
            requests: List of synthesis requests.
            output_dir: Directory for output files.
            merge_strategy: Whether to produce separate files or one
                merged file.
            pause_ms: Pause duration in milliseconds between segments
                when merging into a single file.

        Returns:
            List of SynthesisResult. When merge_strategy is
            ONE_FILE_PER_BATCH, the list contains a single result.
        """
        if not requests:
            return []

        output_dir.mkdir(parents=True, exist_ok=True)

        if merge_strategy == MergeStrategy.ONE_FILE_PER_INPUT:
            return self._synthesize_batch_separate(requests, output_dir)
        return self._synthesize_batch_merged(requests, output_dir, pause_ms)

    def synthesize_pair(
        self,
        text_1: str,
        voice_1: SynthesisRequest,
        text_2: str,
        voice_2: SynthesisRequest,
        output_path: Path,
        pause_ms: int = 500,
    ) -> SynthesisResult:
        """Synthesize two texts and stitch them with a pause.

        Produces a single MP3: [text_1 audio] [pause] [text_2 audio].
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            path_1 = tmp_dir / "part1.mp3"
            path_2 = tmp_dir / "part2.mp3"

            result_1 = self._provider.synthesize(voice_1, path_1)
            result_2 = self._provider.synthesize(voice_2, path_2)

            stitch_audio([path_1, path_2], output_path, pause_ms)

        voice_parts = [v for v in (result_1.voice, result_2.voice) if v]
        combined_voice = "+".join(voice_parts) if voice_parts else None
        return SynthesisResult(
            path=output_path,
            text=f"{text_1} | {text_2}",
            provider=result_1.provider,
            voice=combined_voice,
            language=result_1.language,
        )

    def synthesize_pair_batch(
        self,
        pairs: list[tuple[SynthesisRequest, SynthesisRequest]],
        output_dir: Path,
        merge_strategy: MergeStrategy = MergeStrategy.ONE_FILE_PER_INPUT,
        pause_ms: int = 500,
    ) -> list[SynthesisResult]:
        """Synthesize multiple pairs, optionally merging into one file."""
        if not pairs:
            return []

        output_dir.mkdir(parents=True, exist_ok=True)

        if merge_strategy == MergeStrategy.ONE_FILE_PER_INPUT:
            return self._pair_batch_separate(pairs, output_dir, pause_ms)
        return self._pair_batch_merged(pairs, output_dir, pause_ms)

    # -- Private helpers --------------------------------------------------

    def _synthesize_batch_separate(
        self,
        requests: list[SynthesisRequest],
        output_dir: Path,
    ) -> list[SynthesisResult]:
        results: list[SynthesisResult] = []
        for req in requests:
            filename = generate_filename(req.text)
            path = output_dir / filename
            results.append(self.synthesize(req, path))
        return results

    def _synthesize_batch_merged(
        self,
        requests: list[SynthesisRequest],
        output_dir: Path,
        pause_ms: int,
    ) -> list[SynthesisResult]:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            tmp_paths: list[Path] = []
            canonical_voice = ""
            provider_id = None
            for i, req in enumerate(requests):
                path = tmp_dir / f"seg_{i:04d}.mp3"
                result = self._provider.synthesize(req, path)
                if i == 0:
                    canonical_voice = result.voice or ""
                    provider_id = result.provider
                tmp_paths.append(path)

            combined_text = " | ".join(r.text for r in requests)
            out_path = output_dir / generate_filename(combined_text, prefix="batch_")
            stitch_audio(tmp_paths, out_path, pause_ms)

        if provider_id is None:
            raise RuntimeError("Missing provider for merged synthesis result")

        return [
            SynthesisResult(
                path=out_path,
                text=combined_text,
                provider=provider_id,
                voice=canonical_voice or None,
            )
        ]

    def _pair_batch_separate(
        self,
        pairs: list[tuple[SynthesisRequest, SynthesisRequest]],
        output_dir: Path,
        pause_ms: int,
    ) -> list[SynthesisResult]:
        results: list[SynthesisResult] = []
        for req_1, req_2 in pairs:
            combined = f"{req_1.text}_{req_2.text}"
            out_path = output_dir / generate_filename(combined, prefix="pair_")
            result = self.synthesize_pair(
                req_1.text, req_1, req_2.text, req_2, out_path, pause_ms
            )
            results.append(result)
        return results

    def _pair_batch_merged(
        self,
        pairs: list[tuple[SynthesisRequest, SynthesisRequest]],
        output_dir: Path,
        pause_ms: int,
    ) -> list[SynthesisResult]:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            pair_paths: list[Path] = []
            provider_id = None

            for i, (req_1, req_2) in enumerate(pairs):
                pair_path = tmp_dir / f"pair_{i:04d}.mp3"
                pair_result = self.synthesize_pair(
                    req_1.text,
                    req_1,
                    req_2.text,
                    req_2,
                    pair_path,
                    pause_ms,
                )
                if provider_id is None:
                    provider_id = pair_result.provider
                pair_paths.append(pair_path)

            all_texts = " | ".join(f"{r1.text}-{r2.text}" for r1, r2 in pairs)
            out_path = output_dir / generate_filename(all_texts, prefix="pairs_")
            stitch_audio(pair_paths, out_path, pause_ms)

        if provider_id is None:
            raise RuntimeError("Missing provider for merged pair synthesis result")

        return [
            SynthesisResult(
                path=out_path,
                text=all_texts,
                provider=provider_id,
                voice="mixed",
            )
        ]


def stitch_audio(segments: list[Path], output_path: Path, pause_ms: int = 500) -> None:
    """Concatenate MP3 files with silence between each segment.

    Args:
        segments: Ordered list of MP3 file paths to concatenate.
        output_path: Where to write the stitched MP3.
        pause_ms: Duration of silence between segments in milliseconds.

    Raises:
        FileNotFoundError: If any segment file does not exist.
        ValueError: If segments list is empty.
    """
    if not segments:
        raise ValueError("segments must not be empty")

    silence: Any = AudioSegment.silent(duration=pause_ms)
    combined: Any = None

    for path in segments:
        if not path.exists():
            raise FileNotFoundError(f"Segment not found: {path}")
        segment: Any = AudioSegment.from_mp3(str(path))  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
        combined = segment if combined is None else combined + silence + segment  # pyright: ignore[reportUnknownVariableType]

    if combined is None:
        raise ValueError("No audio segments to stitch")

    tail: Any = AudioSegment.silent(duration=TRAILING_SILENCE_MS)
    combined = combined + tail  # pyright: ignore[reportUnknownVariableType]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.export(str(output_path), format="mp3")  # pyright: ignore[reportUnknownMemberType]
    logger.info("Stitched %d segments → %s", len(segments), output_path)
