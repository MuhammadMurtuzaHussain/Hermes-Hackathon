import sys, soundfile as sf, numpy as np
from kokoro import KPipeline
lines = [l.strip() for l in open(sys.argv[1]) if l.strip()]
voice = sys.argv[2]
pipe = KPipeline(lang_code=voice[0])
for i, line in enumerate(lines):
    chunks = [a for _, _, a in pipe(line, voice=voice, speed=1.0)]
    audio = np.concatenate(chunks)
    sf.write(f"line{i:02d}.wav", audio, 24000)
    print(i, round(len(audio) / 24000, 2), line[:50], flush=True)
