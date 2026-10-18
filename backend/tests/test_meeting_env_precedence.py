import os
import subprocess
import sys
from pathlib import Path


def test_unified_env_is_loaded_before_legacy_env():
    repo=Path(__file__).resolve().parents[2]
    environment=dict(os.environ)
    for key in ("INTELLIGENCE_ENABLED","ENABLE_TRANSCRIPT_CORRECTION","ENABLE_ARCHITECTURE_AGENT_V2","ENABLE_DIAGRAM_MAPPER_V2"):
        environment.pop(key,None)
    command="""import sys;sys.path.insert(0,'backend');from app.meeting.config import MeetingSettings;s=MeetingSettings();print(s.intelligence_enabled,s.enable_transcript_correction,s.enable_architecture_agent_v2,s.enable_diagram_mapper_v2)"""
    result=subprocess.run([sys.executable,"-c",command],cwd=repo,env=environment,text=True,capture_output=True,check=True)
    assert result.stdout.strip()=="True False True True"
