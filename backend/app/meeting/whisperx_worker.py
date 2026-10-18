from __future__ import annotations
import argparse, base64, json, logging, os, sys, traceback, warnings

def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n"); sys.stdout.flush()

def asr_options(args):
    """WhisperX 3.x configures decoding on load, not per transcribe call."""
    result={}
    if args.initial_prompt: result["initial_prompt"]=args.initial_prompt
    if args.hotwords: result["hotwords"]=args.hotwords
    return result

def main():
    p=argparse.ArgumentParser(); p.add_argument("--model", required=True); p.add_argument("--device", default="cpu")
    p.add_argument("--compute-type", default="int8"); p.add_argument("--language"); p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--vad-method", default="pyannote"); p.add_argument("--vad-onset", type=float, default=.5)
    p.add_argument("--vad-offset", type=float, default=.363); p.add_argument("--initial-prompt", default="")
    p.add_argument("--hotwords", default=""); args=p.parse_args()
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/meeting-audio-matplotlib")
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("ai_meeting_draw.meeting.whisperx_worker")
    warnings.filterwarnings("ignore", message=r"(?s).*torchcodec is not installed correctly.*")
    protocol=sys.stdout; sys.stdout=sys.stderr
    import numpy as np
    import whisperx
    # WhisperX reports silence as a warning. Silence is an expected result for
    # streamed meeting segments; the parent process records it at DEBUG.
    logging.getLogger("whisperx.vads.pyannote").setLevel(logging.ERROR)
    logger.info("Loading WhisperX model=%s device=%s vad=%s", args.model, args.device, args.vad_method)
    model=whisperx.load_model(args.model,args.device,compute_type=args.compute_type,language=args.language,
                              vad_method=args.vad_method,
                              vad_options={"vad_onset":args.vad_onset,"vad_offset":args.vad_offset},
                              asr_options=asr_options(args))
    logger.info("WhisperX model loaded")
    sys.stdout=protocol; send({"type":"ready"})
    for line in sys.stdin:
        try:
            pcm=base64.b64decode(json.loads(line)["audio"],validate=True)
            audio=np.frombuffer(pcm,dtype="<i2").astype(np.float32)/32768.0
            logger.debug("Transcribing in-memory waveform samples=%d", len(audio))
            # FasterWhisperPipeline.transcribe only accepts batching/language/
            # chunking controls. Prompt and hotwords were bound in load_model.
            sys.stdout=sys.stderr; result=model.transcribe(audio,batch_size=args.batch_size); sys.stdout=protocol
            segments=result.get("segments",[])
            text=" ".join(s.get("text","").strip() for s in segments).strip()
            probabilities=[s.get("avg_logprob") for s in segments if s.get("avg_logprob") is not None]
            no_speech=[s.get("no_speech_prob") for s in segments if s.get("no_speech_prob") is not None]
            avg_logprob=sum(probabilities)/len(probabilities) if probabilities else None
            confidence=max(0.0,min(1.0,__import__("math").exp(avg_logprob))) if avg_logprob is not None else None
            logger.debug("WhisperX completed characters=%d segments=%d", len(text), len(segments))
            send({"text":text,"language":result.get("language"),"confidence":confidence,
                  "average_log_probability":avg_logprob,
                  "no_speech_probability":max(no_speech) if no_speech else None,"segment_count":len(segments)})
        except Exception as exc:
            logger.exception("WhisperX request failed")
            sys.stdout=protocol; traceback.print_exc(file=sys.stderr); send({"error":str(exc)})
if __name__ == "__main__": main()
