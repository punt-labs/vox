"""Tests for punt_vox.voxd.speech_preparation -- request text to provider request."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import pytest

from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd.speech_preparation import SpeechPreparation


class TestSpeechBodyMemo:
    """The normalized-body memo is bounded and keyed on what normalization reads."""

    def test_expressive_and_plain_bodies_are_kept_apart(self) -> None:
        SpeechPreparation._speech_body.cache_clear()
        text = "[sighs] Run `myTool --dry_run` now."

        plain = SpeechPreparation._speech_body(text, expressive=False)
        expressive = SpeechPreparation._speech_body(text, expressive=True)

        assert plain == "Run my tool dry run now."
        assert expressive == "[sighs] Run my tool dry run now."
        assert SpeechPreparation._speech_body.cache_info().currsize == 2

    def test_providers_share_a_body_only_when_expressiveness_matches(self) -> None:
        SpeechPreparation._speech_body.cache_clear()
        text = "[sighs] hello"
        specs = [
            SynthesisSpec(provider="espeak"),
            SynthesisSpec(provider="openai"),
            SynthesisSpec(provider="elevenlabs", model="eleven_v3"),
        ]

        bodies = [SpeechPreparation.audio_request(text, s).text for s in specs]

        assert bodies == ["hello", "hello", "[sighs] hello"]
        info = SpeechPreparation._speech_body.cache_info()
        assert (info.hits, info.misses) == (1, 2)

    def test_memo_holds_at_most_sixteen_texts(self) -> None:
        SpeechPreparation._speech_body.cache_clear()
        for i in range(40):
            SpeechPreparation._speech_body(f"text {i}", expressive=False)

        info = SpeechPreparation._speech_body.cache_info()
        assert (info.maxsize, info.currsize) == (16, 16)


class TestModelSupportsExpressiveTags:
    """model_supports_expressive_tags lookup for vibe-tag gating."""

    def test_elevenlabs_v3_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        assert (
            SpeechPreparation.model_supports_expressive_tags("elevenlabs", "eleven_v3")
            is True
        )

    def test_elevenlabs_flash_does_not_support(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        assert (
            SpeechPreparation.model_supports_expressive_tags(
                "elevenlabs", "eleven_flash_v2_5"
            )
            is False
        )

    def test_elevenlabs_default_supported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        assert (
            SpeechPreparation.model_supports_expressive_tags("elevenlabs", None) is True
        )

    def test_polly_never_supports(self) -> None:
        assert SpeechPreparation.model_supports_expressive_tags("polly", None) is False
        assert (
            SpeechPreparation.model_supports_expressive_tags("polly", "neural") is False
        )

    def test_openai_never_supports(self) -> None:
        assert SpeechPreparation.model_supports_expressive_tags("openai", None) is False
        assert (
            SpeechPreparation.model_supports_expressive_tags("openai", "tts-1-hd")
            is False
        )

    def test_say_never_supports(self) -> None:
        assert SpeechPreparation.model_supports_expressive_tags("say", None) is False

    def test_espeak_never_supports(self) -> None:
        assert SpeechPreparation.model_supports_expressive_tags("espeak", None) is False

    def test_unknown_provider_never_supports(self) -> None:
        assert (
            SpeechPreparation.model_supports_expressive_tags("future_provider", "any")
            is False
        )


class TestApplyVibeForSynthesis:
    """Normalization gates vibe-tag handling on model capability."""

    @pytest.mark.parametrize(
        ("provider", "model", "expected"),
        [
            ("espeak", None, "Run draft now. Read the guide next."),
            (
                "elevenlabs",
                "eleven_v3",
                "[whispering] Run draft now. Read the guide next.",
            ),
        ],
    )
    def test_markdown_link_labels_are_spoken(
        self, provider: str, model: str | None, expected: str
    ) -> None:
        text = (
            "[whispering] Run [draft](https://example.com) now. "
            "Read [the guide](https://example.com/guide) next."
        )
        request = SpeechPreparation.audio_request(
            text, SynthesisSpec(provider=provider, model=model)
        )
        assert request.text == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("[[draft]](/docs)", "draft"),
            ("[~~~ fences](/manual) then `$5`", "fences then dollar sign 5"),
            (r"Read [C\+\+](/docs) and [R&amp;D](/docs).", "Read C plus plus and R&D."),
            (r"[$5\`](/x)[$5\`](/y)", "5 dollars 5 dollars"),
        ],
    )
    @pytest.mark.parametrize(
        ("provider", "model"), [("espeak", None), ("elevenlabs", "eleven_v3")]
    )
    def test_markdown_labels_stay_prose(
        self, text: str, expected: str, provider: str, model: str | None
    ) -> None:
        request = SpeechPreparation.audio_request(
            text, SynthesisSpec(provider=provider, model=model)
        )
        assert request.text == expected

    @staticmethod
    def _apply(raw: str, tags: str | None, provider: str, model: str | None) -> str:
        body, vibe_tags = SpeechPreparation._normalize(
            raw, SynthesisSpec(provider=provider, model=model, vibe_tags=tags)
        )
        return f"{vibe_tags} {body}" if vibe_tags and body else body

    def test_v3_preserves_vibe_tags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = self._apply("Hello world", "[excited]", "elevenlabs", "eleven_v3")
        assert result == "[excited] Hello world"

    def test_expressive_model_passthrough_when_no_vibe_tags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = self._apply("Hello world", None, "elevenlabs", "eleven_v3")
        assert result == "Hello world"

    def test_v3_preserves_user_tags_in_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = self._apply("[whisper] Quiet message", None, "elevenlabs", "eleven_v3")
        assert result == "[whisper] Quiet message"

    def test_non_expressive_model_drops_vibe_tags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = self._apply(
            "Hello world", "[serious]", "elevenlabs", "eleven_flash_v2_5"
        )
        assert result == "Hello world"
        assert "[serious]" not in result
        assert "serious" not in result

    def test_non_expressive_model_strips_user_tags_from_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = self._apply(
            "[serious] Hello world", None, "elevenlabs", "eleven_flash_v2_5"
        )
        assert result == "Hello world"
        assert "serious" not in result

    def test_non_expressive_model_strips_user_tags_even_with_vibe_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = self._apply(
            "[whisper] Quiet message",
            "[excited]",
            "elevenlabs",
            "eleven_flash_v2_5",
        )
        assert result == "Quiet message"
        assert "whisper" not in result
        assert "excited" not in result

    def test_polly_strips_user_tags(self) -> None:
        result = self._apply("[serious] Important", "[calm]", "polly", None)
        assert result == "Important"

    def test_openai_strips_user_tags(self) -> None:
        result = self._apply("[whisper] Hello", None, "openai", "tts-1")
        assert result == "Hello"

    def test_say_strips_user_tags(self) -> None:
        result = self._apply("[excited] Greetings", "[happy]", "say", None)
        assert result == "Greetings"

    def test_espeak_strips_user_tags(self) -> None:
        result = self._apply("[serious] Notice", None, "espeak", None)
        assert result == "Notice"

    def test_degenerate_text_only_tags_non_expressive_returns_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = self._apply("[serious]", None, "elevenlabs", "eleven_flash_v2_5")
        assert result == ""

    def test_degenerate_text_only_tags_v3_preserves(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = self._apply("[sighs]", None, "elevenlabs", "eleven_v3")
        assert result == "[sighs]"

    def test_normalizes_body_through_production_call_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = self._apply("[serious] my_function works", None, "polly", None)
        assert result == "my function works"
        assert "serious" not in result
        assert "[" not in result
        assert "_" not in result

    def test_v3_normalizes_body_and_preserves_tags(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = self._apply(
            "[whisper] my_function works", None, "elevenlabs", "eleven_v3"
        )
        assert result == "[whisper] my function works"

    def test_v3_speaks_suffix_amount_but_not_inline_code(self) -> None:
        result = self._apply("[whisper] 5€ or `5€`.", None, "elevenlabs", "eleven_v3")
        assert result == "[whisper] 5 euros or 5."

    @pytest.mark.parametrize("model", ["eleven_v3", "eleven_multilingual_v2"])
    @pytest.mark.parametrize(
        ("prefix", "suffix", "expected"),
        [
            ("$12.50", "12.50$", "12 dollars and 50 cents"),
            ("$1.01", "1.01$", "1 dollar and 1 cent"),
            ("-$.05", "-.05$", "minus 5 cents"),
            ("$0.5", "0.5$", "50 cents"),
            ("$1.00", "1.00$", "1 dollar"),
            ("$0.00", "0.00$", "0 dollars"),
            ("$1.005", "1.005$", "1.005 dollars"),
            ("€12.50", "12.50€", "12 euros and 50 cents"),
            ("€1.01", "1.01€", "1 euro and 1 cent"),
            ("€0.50", "0.50€", "50 euro cents"),
            ("€.01", ".01€", "1 euro cent"),
            ("£12.50", "12.50£", "12 pounds and 50 pence"),
            ("£1,000.25", "1,000.25£", "1,000 pounds and 25 pence"),
            ("£1.01", "1.01£", "1 pound and 1 penny"),
            ("£.01", ".01£", "1 penny"),
        ],
    )
    def test_decimal_currency_prefix_and_suffix_speak_alike(
        self, model: str, prefix: str, suffix: str, expected: str
    ) -> None:
        prefixed = self._apply(prefix, None, "elevenlabs", model)
        suffixed = self._apply(suffix, None, "elevenlabs", model)
        assert prefixed == suffixed == expected

    @pytest.mark.parametrize("model", ["eleven_v3", "eleven_multilingual_v2"])
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("€1,50", "1 euro and 50 cents"),
            ("I paid €1,50.", "I paid 1 euro and 50 cents."),
            ("€1,50, then", "1 euro and 50 cents, then"),
            ("€0,50", "50 euro cents"),
            ("1,50€", "1 euro and 50 cents"),
            ("€1,500", "1,500 euros"),
            ("€1,000,000", "1,000,000 euros"),
            ("$1,50", "1,50"),
            ("`€1,50`", "euro sign 1,50"),
            ("`1,50€`", "1,50"),
            ("€1,5", "1,5"),
            ("€1,505", "1,505 euros"),
            ("€1,50,000", "1,50,000"),
            ("€1,50.25", "1,50.25"),
            ("€1,501,50", "1,501 euros and 50 cents"),
            ("€1,50\u2013€2,01", "1 euro and 50 cents to 2 euros and 1 cent"),
            ("€1.50\u20132,50", "1.50 to 2.50 euros"),
            ("€1,50\u20132.50", "1.50 to 2.50 euros"),
            ("€1,50-2.50", "1.50 to 2.50 euros"),
            ("€1.50-2,50", "1.50 to 2.50 euros"),
            ("€1,500,50-2,500,50", "1,500.50 to 2,500.50 euros"),
            ("$1,50-2,50", "1,50-2,50"),
            ("€1.50-2.50", "1.50-2.50"),
            ("1.50\u20132.50€", "1.50\u20132.50"),
            ("1,50\u20132,50€", "1,50\u20132,50"),
        ],
    )
    def test_euro_comma_decimals(self, model: str, source: str, expected: str) -> None:
        assert self._apply(source, None, "elevenlabs", model) == expected

    @pytest.mark.parametrize("model", ["eleven_v3", "eleven_multilingual_v2"])
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("€1.234,56", "1,234 euros and 56 cents"),
            ("1.234,56€", "1,234 euros and 56 cents"),
            ("€1.234.567,89", "1,234,567 euros and 89 cents"),
            ("It costs 1.234,56€.", "It costs 1,234 euros and 56 cents."),
            ("€1.234,56\u20132.345,67", "1,234.56 to 2,345.67 euros"),
            ("€1.234,56-2.345,67", "1,234.56 to 2,345.67 euros"),
            ("€1.234,56\u20132.345", "1,234.56 to 2,345 euros"),
            ("€1.234,56-2.345", "1,234.56 to 2,345 euros"),
            ("€1.234,56\u20132.345.678", "1,234.56 to 2,345,678 euros"),
            ("€1.234,56\u20132.345,10", "1,234.56 to 2,345.10 euros"),
            ("€1,56\u20132.345", "1.56 to 2.345 euros"),
            (
                "€1.234,56\u2013€2.345,67",
                "1,234 euros and 56 cents to 2,345 euros and 67 cents",
            ),
            ("€1.234", "1.234 euros"),
            ("€1.50", "1 euro and 50 cents"),
            ("€1.234,567", "1.234,567"),
            ("$1.234,56", "1.234,56"),
            ("`€1.234,56`", "euro sign 1.234,56"),
        ],
    )
    def test_euro_dotted_thousands(
        self, model: str, source: str, expected: str
    ) -> None:
        assert self._apply(source, None, "elevenlabs", model) == expected

    @pytest.mark.parametrize("model", ["eleven_v3", "eleven_multilingual_v2"])
    @pytest.mark.parametrize("separator", ["\u2013", "-"])
    def test_euro_comma_decimal_bare_range(self, model: str, separator: str) -> None:
        dot_range = self._apply("€1.50\u20132.50", None, "elevenlabs", model)
        assert dot_range == "1.50 to 2.50 euros"
        assert (
            self._apply(f"€1,50{separator}2,50", None, "elevenlabs", model) == dot_range
        )

    @pytest.mark.parametrize("model", ["eleven_v3", "eleven_multilingual_v2"])
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("€1,50-2.50£", "1,50-2.50"),
            ("€1.50\u20132.50£", "1.50\u20132.50"),
            ("It cost (€1,50-2.50£).", "It cost 1,50-2.50."),
        ],
    )
    def test_bare_currency_range_rejects_suffix_sign(
        self, model: str, source: str, expected: str
    ) -> None:
        assert self._apply(source, None, "elevenlabs", model) == expected

    @pytest.mark.parametrize("model", ["eleven_v3", "eleven_multilingual_v2"])
    def test_currency_range_with_slash_before_sign(self, model: str) -> None:
        assert self._apply("¥140\u2013150/$", None, "elevenlabs", model) == (
            "140 to 150 yen"
        )

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("eleven_v3", "[whisper] 5 euros."),
            ("eleven_multilingual_v2", "5 euros."),
        ],
    )
    def test_speaks_suffix_amount_adjacent_to_tag(
        self, model: str, expected: str
    ) -> None:
        assert self._apply("[whisper]5€.", None, "elevenlabs", model) == expected

    def test_trailing_tags_stripped_non_expressive(self) -> None:
        result = self._apply("hello [alert] [serious]", None, "polly", None)
        assert result == "hello"
        assert "alert" not in result
        assert "serious" not in result

    @pytest.mark.parametrize(
        ("model", "expected"),
        [("eleven_v3", "5 euros [laugh]"), ("eleven_multilingual_v2", "5 euros")],
    )
    def test_speaks_suffix_amount_before_adjacent_tag(
        self, model: str, expected: str
    ) -> None:
        assert self._apply("5€[laugh]", None, "elevenlabs", model) == expected

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("eleven_v3", "5 euros [pause] 10 years ago"),
            ("eleven_multilingual_v2", "5 euros 10 years ago"),
        ],
    )
    def test_keeps_tag_boundary_between_suffix_amount_and_number(
        self, model: str, expected: str
    ) -> None:
        assert (
            self._apply("5€[pause]10 years ago", None, "elevenlabs", model) == expected
        )

    @pytest.mark.parametrize("model", ["eleven_v3", "eleven_multilingual_v2"])
    @pytest.mark.parametrize(
        ("spaced", "attached"),
        [
            ("$1 million[pause]10 years ago", "$1million[pause]10 years ago"),
            ("$1 million,[pause]10 years ago", "$1million,[pause]10 years ago"),
            (
                "We saved $1 million—enough to retire",
                "We saved $1million—enough to retire",
            ),
            ("$1 million[pause]€2 million", "$1million[pause]€2million"),
            ("$1 million-[pause]$2 million", "$1million-[pause]$2million"),
            ("$1 million—€2 billion", "$1million—€2billion"),
            ("$1 million\u2019s worth", "$1million\u2019s worth"),
        ],
    )
    def test_spaced_currency_scale_matches_attached(
        self, model: str, spaced: str, attached: str
    ) -> None:
        assert self._apply(spaced, None, "elevenlabs", model) == self._apply(
            attached, None, "elevenlabs", model
        )

    def test_inline_tags_stripped_non_expressive(self) -> None:
        result = self._apply("hello [serious] world", None, "polly", None)
        assert result == "hello world"
        assert "serious" not in result

    def test_leading_tags_still_stripped_non_expressive(self) -> None:
        result = self._apply("[serious] hello", None, "polly", None)
        assert result == "hello"
        assert "serious" not in result

    def _patch_expressive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Force model_supports_expressive_tags to return True."""

        def _always_expressive(_provider: str, _model: str | None) -> bool:
            return True

        monkeypatch.setattr(
            "punt_vox.voxd.speech_preparation.SpeechPreparation.model_supports_expressive_tags",
            _always_expressive,
        )

    def test_trailing_tags_preserved_expressive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_expressive(monkeypatch)
        result = self._apply("hello [serious]", None, "elevenlabs", "eleven_v3")
        assert "[serious]" in result
        assert "hello" in result

    def test_inline_tags_preserved_expressive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_expressive(monkeypatch)
        result = self._apply("hello [serious] world", None, "elevenlabs", "eleven_v3")
        assert "[serious]" in result
        assert "hello" in result
        assert "world" in result

    def test_session_vibe_tags_prepended_expressive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_expressive(monkeypatch)
        result = self._apply("hello", "[warm]", "elevenlabs", "eleven_v3")
        assert result == "[warm] hello"

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            (
                "eleven_v3",
                "Run dollar sign 1 [warm] dollar sign 2 then pay 5 dollars",
            ),
            (
                "eleven_flash_v2_5",
                "Run dollar sign 1 dollar sign 2 then pay 5 dollars",
            ),
        ],
    )
    def test_code_currency_span_survives_tag_handling(
        self, model: str, expected: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        raw = "Run `$1 [warm] $2` then pay $5"
        assert self._apply(raw, None, "elevenlabs", model) == expected

    @pytest.mark.parametrize(
        ("model", "prefix"),
        [("eleven_v3", "[warm] "), ("eleven_flash_v2_5", "")],
    )
    def test_tag_before_fence_does_not_hide_following_code_span(
        self, model: str, prefix: str
    ) -> None:
        raw = "[warm] ```sh\necho\n```\nUse `$1` then pay $5"
        assert self._apply(raw, None, "elevenlabs", model) == (
            f"{prefix}sh echo Use dollar sign 1 then pay 5 dollars"
        )

    def test_tagged_fence_marker_inside_code_is_not_a_closer(self) -> None:
        raw = "```sh\n[warm] ```\nInside `$1`\n[warm] ```\n```\n\nOutside `$2`"
        assert self._apply(raw, None, "elevenlabs", "eleven_flash_v2_5") == (
            "sh Inside 1 dollar Outside dollar sign 2"
        )
