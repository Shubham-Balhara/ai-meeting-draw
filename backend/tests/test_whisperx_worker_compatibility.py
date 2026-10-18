from argparse import Namespace
import inspect

from app.meeting import whisperx_worker


def test_prompt_and_hotwords_are_load_time_asr_options():
    options=whisperx_worker.asr_options(Namespace(initial_prompt="system design",hotwords="Kafka,Redis"))
    assert options=={"initial_prompt":"system design","hotwords":"Kafka,Redis"}
    source=inspect.getsource(whisperx_worker.main)
    assert "asr_options=asr_options(args)" in source
    assert "model.transcribe(audio,batch_size=args.batch_size)" in source
    assert "model.transcribe(audio,**kwargs)" not in source
