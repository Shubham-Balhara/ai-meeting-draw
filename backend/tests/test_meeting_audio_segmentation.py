from array import array

from app.meeting.config import MeetingSettings
from app.meeting.transcription import BYTES_PER_SECOND, UtteranceSegmenter


def pcm(value: int, milliseconds: int) -> bytes:
    samples = 16_000 * milliseconds // 1000
    return array("h", [value] * samples).tobytes()


def settings(**overrides):
    values = dict(
        utterance_vad_enabled=True,
        vad_frame_ms=20,
        vad_energy_threshold=350,
        vad_onset_ms=100,
        vad_offset_ms=200,
        vad_pre_speech_ms=100,
        vad_min_speech_ms=100,
        vad_max_utterance_seconds=2,
    )
    values.update(overrides)
    return MeetingSettings(**values)


def test_silence_is_ignored():
    segmenter = UtteranceSegmenter(settings())
    assert segmenter.feed(pcm(0, 1000)) == []
    assert segmenter.finish() == []


def test_speech_is_emitted_with_padding_and_absolute_timestamps():
    segmenter = UtteranceSegmenter(settings())
    output = []
    for block in (pcm(0, 400), pcm(2000, 500), pcm(0, 300)):
        output.extend(segmenter.feed(block))
    output.extend(segmenter.finish())
    assert len(output) == 1
    start, end, audio = output[0]
    assert 0.28 <= start <= 0.32
    assert 1.08 <= end <= 1.12
    assert len(audio) == round((end - start) * BYTES_PER_SECOND)


def test_multiple_utterances_keep_monotonic_source_time():
    segmenter = UtteranceSegmenter(settings(vad_pre_speech_ms=0))
    output = segmenter.feed(
        pcm(1800, 300) + pcm(0, 300) + pcm(1800, 300) + pcm(0, 300)
    ) + segmenter.finish()
    assert len(output) == 2
    assert output[0][0] < output[0][1] <= output[1][0] < output[1][1]


def test_long_speech_is_bounded():
    segmenter = UtteranceSegmenter(settings(vad_max_utterance_seconds=0.5, vad_pre_speech_ms=0))
    output = segmenter.feed(pcm(2000, 1200)) + segmenter.finish()
    assert len(output) >= 2
    assert all(end - start <= 0.501 for start, end, _ in output)
