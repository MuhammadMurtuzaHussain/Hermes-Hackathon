"""Place each narration line on its scene and mix one track. Usage: mix.py <dir with lineNN.wav> <out.wav>"""
import subprocess, sys
from pathlib import Path
STARTS = [0.3, 6.4, 12.4, 23.4, 29.6, 35.4, 42.4, 50.3, 59.7, 64.6, 70.4, 75.4, 83.2]  # seconds, one per line
SCENE_ENDS = [6, 12, 23, 29.6, 35, 42, 50, 53, 64, 70, 75, 83, 89.5]
src, out = Path(sys.argv[1]), sys.argv[2]
args, chains = [], []
for i, start in enumerate(STARTS):
    wav = src / f"line{i:02d}.wav"
    length = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)]))
    end = start + length
    print(f"line {i:2d}: {start:5.1f}s to {end:5.1f}s (scene ends {SCENE_ENDS[i]})" + ("  OVERRUNS" if end > SCENE_ENDS[i] + 0.3 else ""))
    args += ["-i", str(wav)]
    ms = int(start * 1000)
    chains.append(f"[{i}:a]adelay={ms}|{ms}[d{i}]")
mix = "".join(f"[d{i}]" for i in range(len(STARTS)))
graph = ";".join(chains) + f";{mix}amix=inputs={len(STARTS)}:normalize=0,apad,atrim=0:89.5,loudnorm=I=-16:TP=-1.5:LRA=11[out]"
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args, "-filter_complex", graph, "-map", "[out]", "-ar", "48000", "-ac", "2", out], check=True)
