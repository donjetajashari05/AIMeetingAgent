# AI Meeting Assistant me Memorie Afatgjate

## Struktura

```
meeting-assistant/
├── scripts/
│   ├── transcribe.py      # transkriptimi me faster-whisper
│   └── evaluate_wer.py    # matja e saktësisë (WER)
├── data/
│   ├── audio/             # regjistrimet e mbledhjeve
│   └── reference/         # transkriptet manuale (të sakta)
├── output/                # rezultatet e transkriptimit
└── requirements.txt
```

## Instalimi

Kërkohet Python 3.10 ose më i ri.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux / macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

Për GPU NVIDIA duhen CUDA 12 dhe cuDNN 9. Pa GPU, skripta punon automatikisht në CPU.

## Përdorimi

1. Vendos regjistrimin në `data/audio/`, p.sh. `mbledhja1.mp3`.

2. Transkriptoje:
   ```bash
   python scripts/transcribe.py data/audio/mbledhja1.mp3
   ```
   Pa GPU, përdor modelin `medium` ose `small`, sepse `large-v3` në CPU është shumë i ngadalshëm:
   ```bash
   python scripts/transcribe.py data/audio/mbledhja1.mp3 --model medium
   ```

3. Shkruaj manualisht transkriptin e saktë në `data/reference/mbledhja1.txt`.

4. Mate saktësinë:
   ```bash
   python scripts/evaluate_wer.py data/reference/mbledhja1.txt output/mbledhja1.txt
   ```

## Eksperimentet e fazës 1

Ruaj rezultatet në një tabelë, se do të të duhen për kapitullin e rezultateve:

| Mbledhja | Gjuha | Modeli | WER | Koha e përpunimit |
|----------|-------|--------|-----|-------------------|
| mbledhja1 | en | large-v3 | | |
| mbledhja2 | sq | large-v3 | | |
| mbledhja3 | sq + en | large-v3 | | |
