from pydub.audio_segment import AudioSegment

class Sine:
    def __init__(self, freq: float) -> None: ...
    def to_audio_segment(
        self, duration: float = 1000.0, volume: float = 0.0
    ) -> AudioSegment: ...
