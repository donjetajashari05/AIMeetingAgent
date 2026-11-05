# AI Meeting Assistant me Memorie Afatgjate

## Struktura

```
meeting-assistant/
├── app/                   # aplikacioni web (FastAPI + faqja)
│   ├── main.py            # API
│   ├── pipeline.py        # përpunimi në sfond: transkriptim → folës → Claude
│   ├── assistant.py       # "Pyet arkivin" (kërkim + përgjigje me burime)
│   ├── db.py              # SQLite: mbledhjet, vendimet, detyrat, indeksi i kërkimit
│   └── static/            # ndërfaqja (HTML, CSS, JS)
├── scripts/
│   ├── transcribe.py      # transkriptimi me faster-whisper
│   ├── diarize.py         # kush po flet (pyannote.audio)
│   ├── extract.py         # përmbledhja, vendimet, detyrat me Claude
│   └── evaluate_wer.py    # matja e saktësisë (WER)
├── data/
│   ├── audio/             # regjistrimet e mbledhjeve
│   ├── reference/         # transkriptet manuale (të sakta)
│   ├── samples/           # transkript shembull për provë
│   ├── uploads/           # skedarët e ngarkuar në aplikacion (jo në git)
│   └── app.db             # databaza e aplikacionit (jo në git)
├── output/                # rezultatet e transkriptimit
└── requirements.txt
```

## Aplikacioni web

```bash
python -m app
```

Pastaj hap http://127.0.0.1:8000 në shfletues. Serveri dëgjon vetëm në këtë kompjuter.

- **Mbledhje e re**: ngarko audio (mp3, wav, m4a, webm…), regjistro direkt nga mikrofoni,
  ose ngarko një transkript `.json` (p.sh. `data/samples/mbledhja_shembull.json`).
- Përpunimi bëhet në sfond: transkriptimi → folësit → vendimet dhe detyrat.
- **Detyrat**: shëno si të kryera; raporti në panel tregon sa janë kryer dhe sa janë me vonesë.
- **Pyet arkivin**: kërkon në të gjitha mbledhjet dhe përgjigjet me burimin (mbledhja + minuta).
- **Ekipi**: punonjësit, ekipet dhe projektet (një punonjës mund të jetë në disa). Shtoji një nga
  një ose importo me CSV (`Emri, Email, Roli, Ekipet, Projektet`, disa grupe ndahen me `;`).
  Kur mbledhja lidhet me një ekip/projekt, Claude i merr emrat e anëtarëve dhe detyrat lidhen
  me punonjësit (edhe sipas nofkave). Përgjegjësi mund të ndërrohet me dorë te detyra.

### Hyrja dhe rolet

Hyrja bëhet me emailin e punës: shkruan emailin, merr një kod 6-shifror (vlen 10 minuta).
Pa SMTP të konfiguruar, kodi shfaqet në terminalin e serverit.

| Roli | Kush | Çka mund të bëjë |
|------|------|------------------|
| Administrator | emailet te `ADMIN_EMAILS` ose të shënuar “Administrator” | gjithçka: ekipet, projektet, punonjësit, të gjitha mbledhjet, njoftimet |
| Project manager | PM-ja i caktuar te projekti (një për projekt) | shton mbledhje në projektet e veta, cakton detyra, i dërgon në Jira; merr me email përmbledhjet dhe përkujtuesin ditor |
| Punonjës | të tjerët | sheh detyrat e veta dhe mbledhjet e projekteve ku është anëtar; shënon detyrat e veta si të kryera |

- **Email (Gmail / Google Workspace)**: `SMTP_USER` + `SMTP_PASSWORD` (App password i Google).
  Pa to, asgjë s’dërgohet: emailet shihen te faqja “Njoftimet”.
- **Jira Cloud**: `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, dhe te çdo projekt “Çelësi në Jira”.
  Butoni “Dërgo në Jira” te mbledhja krijon tiketat, të caktuara sipas emailit të punonjësit.

Çelësat vendosen në `.env` (shih `.env.example`), pastaj rinise serverin:

| Çelësi | Për çka | Pa të |
|--------|---------|-------|
| `ANTHROPIC_API_KEY` | përmbledhja, vendimet, detyrat, përgjigjet | vetëm transkripti dhe kërkimi i thjeshtë |
| `HF_TOKEN` | kush po flet (Folësi 1, 2…) | transkripti pa emrat e folësve |

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
