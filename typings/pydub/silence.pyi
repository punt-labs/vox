from pydub.audio_segment import AudioSegment

def detect_leading_silence(
    sound: AudioSegment, silence_threshold: float = -50.0, chunk_size: int = 10
) -> int: ...
